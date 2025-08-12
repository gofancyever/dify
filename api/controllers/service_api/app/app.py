
from typing import cast

import flask_restful
from flask_restful import Resource, fields, marshal_with, reqparse
from sqlalchemy import select
from sqlalchemy.orm import Session
from werkzeug.exceptions import Forbidden

from controllers.service_api import api
from controllers.service_api.app.error import AppUnavailableError
from controllers.service_api.wraps import get_app_model, validate_app_token, validate_sf_token
from core.app.app_config.common.parameters_mapping import get_parameters_from_feature_dict
from extensions.ext_database import db
from libs.helper import TimestampField
from models import Account
from models.model import ApiToken, App, AppMode
from services.app_dsl_service import AppDslService, ImportStatus
from services.app_service import AppService

ALLOW_CREATE_APP_MODES = ["chat", "agent-chat", "advanced-chat", "workflow", "completion"]
api_key_fields = {
    "id": fields.String,
    "type": fields.String,
    "token": fields.String,
    "last_used_at": TimestampField,
    "created_at": TimestampField,
}


class AppParameterApi(Resource):
    """Resource for app variables."""

    @validate_app_token
    def get(self, app_model: App):
        """Retrieve app parameters."""
        if app_model.mode in {AppMode.ADVANCED_CHAT.value, AppMode.WORKFLOW.value}:
            workflow = app_model.workflow
            if workflow is None:
                raise AppUnavailableError()

            features_dict = workflow.features_dict
            user_input_form = workflow.user_input_form(to_old_structure=True)
        else:
            app_model_config = app_model.app_model_config
            if app_model_config is None:
                raise AppUnavailableError()

            features_dict = app_model_config.to_dict()

            user_input_form = features_dict.get("user_input_form", [])

        return get_parameters_from_feature_dict(features_dict=features_dict, user_input_form=user_input_form)


class AppMetaApi(Resource):
    @validate_app_token
    def get(self, app_model: App):
        """Get app meta"""
        return AppService().get_app_meta(app_model)


class AppInfoApi(Resource):
    @validate_app_token
    def get(self, app_model: App):
        """Get app information"""
        tags = [tag.name for tag in app_model.tags]
        return {"name": app_model.name, "description": app_model.description, "tags": tags, "mode": app_model.mode}


class AppApi(Resource):
    @validate_sf_token
    @get_app_model
    def delete(self,account,app_model):
        print(account,app_model)
        app_service = AppService()
        app_service.delete_app(app_model)
        return { 'id': app_model.id }
class AppListApi(Resource):
    @validate_sf_token
    def post(self,account):
        parser = reqparse.RequestParser()
        parser.add_argument("name", type=str, required=True, location="json")
        parser.add_argument("description", type=str, location="json")
        parser.add_argument("mode", type=str, choices=ALLOW_CREATE_APP_MODES, location="json")
        parser.add_argument("icon_type", type=str, location="json")
        parser.add_argument("icon", type=str, location="json")
        parser.add_argument("icon_background", type=str, location="json")
        args = parser.parse_args()
    
        app_service = AppService()
        app = app_service.create_app(account.current_tenant.id, args, account)
        return { 'id': app.id }
    
class AppImportApi(Resource):
    @validate_sf_token
    def post(self,account):
        parser = reqparse.RequestParser()
        parser.add_argument("mode", type=str, required=True, location="json")
        parser.add_argument("yaml_content", type=str, location="json")
        parser.add_argument("yaml_url", type=str, location="json")
        parser.add_argument("name", type=str, location="json")
        parser.add_argument("description", type=str, location="json")
        parser.add_argument("icon_type", type=str, location="json")
        parser.add_argument("icon", type=str, location="json")
        parser.add_argument("icon_background", type=str, location="json")
        parser.add_argument("app_id", type=str, location="json")
        args = parser.parse_args()

        # Create service with session
        with Session(db.engine) as session:
            import_service = AppDslService(session)
            # Import app
            account = cast(Account, account)
            result = import_service.import_app(
                account=account,
                import_mode=args["mode"],
                yaml_content=args.get("yaml_content"),
                yaml_url=args.get("yaml_url"),
                name=args.get("name"),
                description=args.get("description"),
                icon_type=args.get("icon_type"),
                icon=args.get("icon"),
                icon_background=args.get("icon_background"),
                app_id=args.get("app_id"),
            )
            session.commit()

        # Return appropriate status code based on result
        status = result.status
        if status == ImportStatus.FAILED.value:
            return result.model_dump(mode="json"), 400
        elif status == ImportStatus.PENDING.value:
            return result.model_dump(mode="json"), 202
        return result.model_dump(mode="json"), 200
    
class AppApiKeyListResource(Resource):
    method_decorators = [validate_sf_token]

    resource_type = "app"
    resource_model = App
    resource_id_field = "app_id"
    token_prefix = "app-"
    max_keys = 10

    def after_request(self, resp):
        resp.headers["Access-Control-Allow-Origin"] = "*"
        resp.headers["Access-Control-Allow-Credentials"] = "true"
        return resp
    
    @marshal_with(api_key_fields)
    def post(self, account,resource_id):
        assert self.resource_id_field is not None, "resource_id_field must be set"
        resource_id = str(resource_id)
        _get_resource(resource_id, account.current_tenant.id, self.resource_model)
        if not account.is_editor:
            raise Forbidden()

        current_key_count = (
            db.session.query(ApiToken)
            .filter(ApiToken.type == self.resource_type, getattr(ApiToken, self.resource_id_field) == resource_id)
            .count()
        )

        if current_key_count >= self.max_keys:
            flask_restful.abort(
                400,
                message=f"Cannot create more than {self.max_keys} API keys for this resource type.",
                code="max_keys_exceeded",
            )

        key = ApiToken.generate_api_key(self.token_prefix, 24)
        api_token = ApiToken()
        setattr(api_token, self.resource_id_field, resource_id)
        api_token.tenant_id = account.current_tenant.id
        api_token.token = key
        api_token.type = self.resource_type
        db.session.add(api_token)
        db.session.commit()
        return api_token, 201
    
def _get_resource(resource_id, tenant_id, resource_model):
    if resource_model == App:
        with Session(db.engine) as session:
            resource = session.execute(
                select(resource_model).filter_by(id=resource_id, tenant_id=tenant_id)
            ).scalar_one_or_none()
    else:
        with Session(db.engine) as session:
            resource = session.execute(
                select(resource_model).filter_by(id=resource_id, tenant_id=tenant_id)
            ).scalar_one_or_none()

    if resource is None:
        flask_restful.abort(404, message=f"{resource_model.__name__} not found.")

    return resource
    
api.add_resource(AppParameterApi, "/parameters")
api.add_resource(AppMetaApi, "/meta")
api.add_resource(AppInfoApi, "/info")
api.add_resource(AppListApi, "/app")
api.add_resource(AppImportApi, "/app/import")
api.add_resource(AppApi, "/app/<uuid:app_id>")
api.add_resource(AppApiKeyListResource, "/app/<uuid:resource_id>/api-keys")