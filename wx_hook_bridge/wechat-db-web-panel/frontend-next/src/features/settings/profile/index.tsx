import { ContentSection } from '../components/content-section'
import { ProfileForm } from './profile-form'

export function SettingsProfile() {
  return (
    <ContentSection title='个人资料' desc='其他用户将在这里看到你的公开资料。'>
      <ProfileForm />
    </ContentSection>
  )
}
