import logging
from datetime import UTC, datetime
from typing import Optional

import requests
from flask import current_app, redirect, request
from flask_restful import Resource
from sqlalchemy import select
from sqlalchemy.orm import Session
from werkzeug.exceptions import Unauthorized

from configs import dify_config
from constants.languages import languages
from events.tenant_event import tenant_was_created
from extensions.ext_database import db
from libs.helper import extract_remote_ip
from libs.oauth import GitHubOAuth, GoogleOAuth, OAuthUserInfo, ShufengOAuth
from models import Account
from models.account import AccountStatus
from services.account_service import AccountService, RegisterService, TenantService
from services.errors.account import AccountNotFoundError, AccountRegisterError
from services.errors.workspace import WorkSpaceNotAllowedCreateError, WorkSpaceNotFoundError
from services.feature_service import FeatureService

from .. import api


def get_oauth_providers():
    with current_app.app_context():
        if not dify_config.GITHUB_CLIENT_ID or not dify_config.GITHUB_CLIENT_SECRET:
            github_oauth = None
        else:
            github_oauth = GitHubOAuth(
                client_id=dify_config.GITHUB_CLIENT_ID,
                client_secret=dify_config.GITHUB_CLIENT_SECRET,
                redirect_uri=dify_config.CONSOLE_API_URL + "/console/api/oauth/authorize/github",
            )
        if not dify_config.GOOGLE_CLIENT_ID or not dify_config.GOOGLE_CLIENT_SECRET:
            google_oauth = None
        else:
            google_oauth = GoogleOAuth(
                client_id=dify_config.GOOGLE_CLIENT_ID,
                client_secret=dify_config.GOOGLE_CLIENT_SECRET,
                redirect_uri=dify_config.CONSOLE_API_URL + "/console/api/oauth/authorize/google",
            )
        if not dify_config.SHUFENG_CLIENT_ID or not dify_config.SHUFENG_CLIENT_SECRET:
            shufeng_oauth = None
        else:
            shufeng_oauth = ShufengOAuth(
                client_id=dify_config.SHUFENG_CLIENT_ID,
                client_secret=dify_config.SHUFENG_CLIENT_SECRET,
                redirect_uri=dify_config.CONSOLE_API_URL + "/console/api/oauth/authorize/shufeng",
            )
        OAUTH_PROVIDERS = {"github": github_oauth, "google": google_oauth, "shufeng": shufeng_oauth}
        return OAUTH_PROVIDERS

class OAuthLogin(Resource):
    def get(self, provider: str):
        invite_token = request.args.get("invite_token") or None
        OAUTH_PROVIDERS = get_oauth_providers()
        with current_app.app_context():
            oauth_provider = OAUTH_PROVIDERS.get(provider)
        if not oauth_provider:
            return {"error": "Invalid provider"}, 400

        auth_url = oauth_provider.get_authorization_url(invite_token=invite_token)
        return redirect(auth_url)


class OAuthCallback(Resource):
    def get(self, provider: str):
        OAUTH_PROVIDERS = get_oauth_providers()
        with current_app.app_context():
            oauth_provider = OAUTH_PROVIDERS.get(provider)
        if not oauth_provider:
            return {"error": "Invalid provider"}, 400

        code = request.args.get("code")
        state = request.args.get("state")
        invite_token = None
        if state:
            invite_token = state

        try:
            token = oauth_provider.get_access_token(code)
            user_info = oauth_provider.get_user_info(token)
        except requests.exceptions.RequestException as e:
            error_text = e.response.text if e.response else str(e)
            logging.exception(f"An error occurred during the OAuth process with {provider}: {error_text}")
            return {"error": "OAuth process failed"}, 400

        if invite_token and RegisterService.is_valid_invite_token(invite_token):
            invitation = RegisterService._get_invitation_by_token(token=invite_token)
            if invitation:
                invitation_email = invitation.get("email", None)
                if invitation_email != user_info.email:
                    return redirect(f"{dify_config.CONSOLE_WEB_URL}/signin?message=Invalid invitation token.")

            return redirect(f"{dify_config.CONSOLE_WEB_URL}/signin/invite-settings?invite_token={invite_token}")

        try:
            account = _generate_account(provider, user_info)
        except AccountNotFoundError:
            return redirect(f"{dify_config.CONSOLE_WEB_URL}/signin?message=Account not found.")
        except (WorkSpaceNotFoundError, WorkSpaceNotAllowedCreateError):
            return redirect(
                f"{dify_config.CONSOLE_WEB_URL}/signin"
                "?message=Workspace not found, please contact system admin to invite you to join in a workspace."
            )
        except AccountRegisterError as e:
            return redirect(f"{dify_config.CONSOLE_WEB_URL}/signin?message={e.description}")

        # Check account status
        if account.status == AccountStatus.BANNED.value:
            return redirect(f"{dify_config.CONSOLE_WEB_URL}/signin?message=Account is banned.")

        if account.status == AccountStatus.PENDING.value:
            account.status = AccountStatus.ACTIVE.value
            account.initialized_at = datetime.now(UTC).replace(tzinfo=None)
            db.session.commit()

        try:
            TenantService.create_owner_tenant_if_not_exist(account)
        except Unauthorized:
            return redirect(f"{dify_config.CONSOLE_WEB_URL}/signin?message=Workspace not found.")
        except WorkSpaceNotAllowedCreateError:
            return redirect(
                f"{dify_config.CONSOLE_WEB_URL}/signin"
                "?message=Workspace not found, please contact system admin to invite you to join in a workspace."
            )

        token_pair = AccountService.login(
            account=account,
            ip_address=extract_remote_ip(request),
        )

        return redirect(
            f"{dify_config.CONSOLE_WEB_URL}?access_token={token_pair.access_token}&refresh_token={token_pair.refresh_token}"
        )


def _get_account_by_openid_or_email(provider: str, user_info: OAuthUserInfo) -> Optional[Account]:
    account: Optional[Account] = Account.get_by_openid(provider, user_info.id)

    if not account:
        with Session(db.engine) as session:
            account = session.execute(select(Account).filter_by(email=user_info.email)).scalar_one_or_none()

    return account


def _generate_account(provider: str, user_info: OAuthUserInfo):
    # Get account by openid or email.
    account = _get_account_by_openid_or_email(provider, user_info)

    if account:
        tenants = TenantService.get_join_tenants(account)
        if not tenants:
            if not FeatureService.get_system_features().is_allow_create_workspace:
                raise WorkSpaceNotAllowedCreateError()
            else:
                new_tenant = TenantService.create_tenant(f"{account.name}'s Workspace")
                TenantService.create_tenant_member(new_tenant, account, role="owner")
                account.current_tenant = new_tenant
                tenant_was_created.send(new_tenant)

    if not account:
        if not FeatureService.get_system_features().is_allow_register:
            raise AccountNotFoundError()
        account_name = user_info.name or "Dify"
        account = RegisterService.register(
            email=user_info.email, name=account_name, password=None, open_id=user_info.id, provider=provider
        )

        # Set interface language
        preferred_lang = request.accept_languages.best_match(languages)
        if preferred_lang and preferred_lang in languages:
            interface_language = preferred_lang
        else:
            interface_language = languages[0]
        account.interface_language = interface_language
        db.session.commit()

    # Link account
    AccountService.link_account_integrate(provider, user_info.id, account)

    return account


class ShufengTokenAuth(Resource):
    def post(self):
        """
        根据数风token获取用户信息并处理登录/注册
        请求参数：
        - sf_token: 数风系统的token
        """
        data = request.get_json()
        if not data or not data.get('sf_token'):
            return {"error": "sf_token is required"}, 400
            
        sf_token = data.get('sf_token')
        
        try:
            # 调用数风接口获取用户信息
            user_info = _get_user_info_from_shufeng_token(sf_token)
            
  
            user_info['email'] = f"{user_info.get('userName')}@dify.ai"
            # 转换为标准用户信息格式
            oauth_user_info = OAuthUserInfo(
                id=str(user_info.get('id', '')),
                name=user_info.get('nickName', user_info.get('userName', 'Dify User')),
                email=user_info.get('email')
            )
            
            # 生成或获取账户
            account = _generate_account('shufeng', oauth_user_info)
            
            # 检查账户状态
            if account.status == AccountStatus.BANNED.value:
                return {"error": "Account is banned"}, 403
                
            if account.status == AccountStatus.PENDING.value:
                account.status = AccountStatus.ACTIVE.value
                account.initialized_at = datetime.now(UTC).replace(tzinfo=None)
                db.session.commit()
                
            try:
                TenantService.create_owner_tenant_if_not_exist(account)
            except Unauthorized:
                return {"error": "Workspace not found"}, 403
            except WorkSpaceNotAllowedCreateError:
                return {"error": "Workspace creation not allowed"}, 403
                
            # 生成token
            token_pair = AccountService.login(
                account=account,
                ip_address=extract_remote_ip(request),
            )
            
            return {
                "access_token": token_pair.access_token,
                "refresh_token": token_pair.refresh_token,
                "user": {
                    "id": account.id,
                    "name": account.name,
                    "email": account.email
                }
            }, 200
            
        except requests.exceptions.RequestException as e:
            logging.exception("Failed to get user info from shufeng")
            return {"error": "Failed to authenticate with shufeng"}, 400
        except (AccountNotFoundError, WorkSpaceNotFoundError, WorkSpaceNotAllowedCreateError) as e:
            return {"error": str(e)}, 400
        except Exception as e:
            logging.exception("Unexpected error in shufeng token auth")
            return {"error": "Internal server error"}, 500


def _get_user_info_from_shufeng_token(sf_token: str) -> dict:
    """
    根据数风token获取用户信息
    """
    headers = {
        'Authorization': f'Bearer {sf_token}',
        'Content-Type': 'application/json'
    }
    
    # 调用数风接口获取用户信息
    # 可以通过环境变量配置数风API地址，默认使用localhost:8000
    shufeng_api_url = getattr(dify_config, 'SHUFENG_API_URL', 'http://beta.shufeng.cn:30080')
    api_url = f"{shufeng_api_url}/api/admin/getInfo"
    
    response = requests.get(api_url, headers=headers)
    response.raise_for_status()
    
    data = response.json()
    
    if data.get('code') != 200000:
        raise ValueError(f"Shufeng API error: {data.get('message', 'Unknown error')}")
        
    result = data.get('result', {})
    user = result.get('user', {})
    
    return user


api.add_resource(OAuthLogin, "/oauth/login/<provider>")
api.add_resource(OAuthCallback, "/oauth/authorize/<provider>")
api.add_resource(ShufengTokenAuth, "/oauth/shufeng/token")
