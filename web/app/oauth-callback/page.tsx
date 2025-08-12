'use client'
import { useRouter, useSearchParams } from 'next/navigation'
import { useEffect, useState } from 'react'
import Toast from '@/app/components/base/toast'
import { verifyShufengToken } from '@/service/common'

const OAuthCallback = () => {
  const searchParams = useSearchParams()
  const router = useRouter()
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const sfToken = searchParams.get('sf_token')
  const redirectUrl = searchParams.get('redirect_url') || '/'

  useEffect(() => {
    const handleShufengAuth = async () => {
      if (!sfToken) {
        setError('Missing sf_token parameter')
        setLoading(false)
        return
      }

      try {
        setLoading(true)

        // 调用后端接口验证 sf_token 并获取 access_token
        const response = await verifyShufengToken({ sf_token: sfToken })

        const { access_token, refresh_token } = response

        // 存储 token 到 localStorage
        if (access_token)
          localStorage.setItem('access_token', access_token)

        if (refresh_token)
          localStorage.setItem('refresh_token', refresh_token)

        // 显示成功消息
        Toast.notify({
          type: 'success',
          message: 'Login successful!',
        })
        // 构建带有 token 的重定向 URL
        const urlWithTokens = new URL(redirectUrl, window.location.origin)
        urlWithTokens.searchParams.set('access_token', encodeURIComponent(access_token))
        urlWithTokens.searchParams.set('refresh_token', encodeURIComponent(refresh_token))
        // 跳转到指定页面或默认首页
        router.replace(urlWithTokens.toString())
      }
 catch (err: any) {
        console.error('Shufeng auth error:', err)
        setError(err.message || 'Authentication failed')

        Toast.notify({
          type: 'error',
          message: err.message || 'Authentication failed',
        })

        // 3秒后跳转到登录页
        setTimeout(() => {
          router.push('/signin')
        }, 3000)
      }
 finally {
        setLoading(false)
      }
    }

    handleShufengAuth()
  }, [sfToken, redirectUrl])

  if (loading) {
    return (
      <div className="flex min-h-screen items-center justify-center">
        <div className="text-center">
          <div className="mx-auto mb-4 h-8 w-8 animate-spin rounded-full border-b-2 border-blue-500"></div>
          <p className="text-gray-600">Authenticating...</p>
        </div>
      </div>
    )
  }

  if (error) {
    return (
      <div className="flex min-h-screen items-center justify-center">
        <div className="text-center">
          <div className="mb-4 text-red-500">
            <svg className="mx-auto h-8 w-8" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
            </svg>
          </div>
          <p className="mb-2 text-red-600">Authentication Failed</p>
          <p className="text-sm text-gray-600">{error}</p>
          <p className="mt-2 text-sm text-gray-500">Redirecting to login page...</p>
        </div>
      </div>
    )
  }

  return (
    <div className="flex min-h-screen items-center justify-center">
      <div className="text-center">
        <div className="mb-4 text-green-500">
          <svg className="mx-auto h-8 w-8" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
          </svg>
        </div>
        <p className="text-green-600">Authentication Successful!</p>
        <p className="text-sm text-gray-600">Redirecting...</p>
      </div>
    </div>
  )
}

export default OAuthCallback
