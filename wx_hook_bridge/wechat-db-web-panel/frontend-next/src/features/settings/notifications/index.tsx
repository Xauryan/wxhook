import { ContentSection } from '../components/content-section'
import { NotificationsForm } from './notifications-form'

export function SettingsNotifications() {
  return (
    <ContentSection title='通知' desc='设置接收通知的内容与方式。'>
      <NotificationsForm />
    </ContentSection>
  )
}
