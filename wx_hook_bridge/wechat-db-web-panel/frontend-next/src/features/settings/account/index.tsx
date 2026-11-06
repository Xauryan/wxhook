import { ContentSection } from '../components/content-section'
import { AccountForm } from './account-form'

export function SettingsAccount() {
  return (
    <ContentSection title='账号设置' desc='更新账号信息及语言等偏好设置。'>
      <AccountForm />
    </ContentSection>
  )
}
