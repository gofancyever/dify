# 数风OAuth集成实现文档

## 概述

本实现为Dify系统添加了数风(Shufeng)OAuth集成功能，允许用户通过数风token进行身份验证和自动登录。

## 实现功能

1. **后端API接口**: 接收数风token，验证用户身份，创建/登录用户账户
2. **前端回调页面**: 处理OAuth回调，存储token，跳转到目标页面
3. **自动用户创建**: 如果用户不存在，根据数风用户信息自动创建账户

## 文件修改

### 后端文件 (API)

#### `api/controllers/console/auth/oauth.py`

**新增接口**: `POST /console/api/oauth/shufeng/token`

**功能**:
- 接收客户端发送的数风token
- 调用数风API获取用户信息
- 根据email查询/创建用户
- 返回Dify的access_token和refresh_token

**请求格式**:
```json
{
  "sf_token": "数风系统的token"
}
```

**响应格式**:
```json
{
  "access_token": "dify_access_token",
  "refresh_token": "dify_refresh_token",
  "user": {
    "id": "user_id",
    "name": "user_name",
    "email": "user_email"
  }
}
```

**新增函数**:
- `ShufengTokenAuth`: RESTful资源类，处理token验证
- `_get_user_info_from_shufeng_token()`: 调用数风API获取用户信息

### 前端文件 (Web)

#### `web/app/oauth-callback/page.tsx`

**功能**:
- 从URL参数获取`sf_token`和`redirect_url`
- 调用后端API验证token
- 存储返回的token到localStorage
- 显示加载/成功/错误状态
- 自动跳转到指定页面

**URL格式**:
```
/oauth-callback?sf_token=TOKEN&redirect_url=/target/path
```

## 数风API集成

### API端点
- **URL**: `http://localhost:8000/api/admin/getInfo`
- **方法**: GET
- **认证**: Bearer Token

### 请求头
```
Authorization: Bearer {sf_token}
Content-Type: application/json
```

### 响应格式
```json
{
  "code": 200000,
  "message": "operate successfully",
  "result": {
    "user": {
      "id": "7213440552216101001",
      "userName": "root",
      "nickName": "系统管理员",
      "email": "gaofan@shufeng.cn",
      "phone": "",
      "status": "0"
    }
  }
}
```

## 配置选项

### 环境变量 (可选)

在`dify_config`中可以配置:
- `SHUFENG_API_URL`: 数风API基础URL (默认: `http://localhost:8000`)

## 使用流程

### 完整OAuth流程

1. **用户访问回调URL**:
   ```
   GET /oauth-callback?sf_token=YOUR_TOKEN&redirect_url=/dashboard
   ```

2. **前端处理**:
   - 解析URL参数
   - 调用后端API验证token

3. **后端处理**:
   - 接收sf_token
   - 调用数风API获取用户信息
   - 查询/创建用户账户
   - 生成Dify token

4. **完成登录**:
   - 前端存储token
   - 跳转到目标页面

### 错误处理

- **token无效**: 显示错误信息，3秒后跳转到登录页
- **网络错误**: 显示连接失败信息
- **服务器错误**: 显示内部错误信息

## 测试

### 运行测试脚本
```bash
python test_shufeng_oauth_api.py
```

### 手动测试步骤

1. 确保Dify API服务运行在localhost:5001
2. 确保Dify Web服务运行在localhost:3000
3. 获取有效的数风token
4. 访问: `http://localhost:3000/oauth-callback?sf_token=YOUR_TOKEN&redirect_url=/`

## 安全考虑

1. **Token验证**: 所有token都通过数风API验证
2. **HTTPS传输**: 生产环境应使用HTTPS
3. **Token存储**: Token存储在localStorage，考虑安全性
4. **错误处理**: 不暴露敏感的错误信息

## 部署注意事项

1. **数风API地址**: 根据部署环境配置正确的数风API地址
2. **CORS设置**: 确保前端可以调用后端API
3. **路由配置**: 确保`/oauth-callback`路由正确配置
4. **日志记录**: 监控OAuth流程的日志

## 维护和监控

- 监控数风API调用成功率
- 记录用户创建和登录日志
- 定期检查token过期和刷新机制
- 监控错误率和响应时间