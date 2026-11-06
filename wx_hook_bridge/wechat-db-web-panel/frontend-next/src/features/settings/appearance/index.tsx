import { ContentSection } from '../components/content-section'
import { AppearanceForm } from './appearance-form'

export function SettingsAppearance() {
  return (
    <ContentSection title='外观' desc='自定义界面外观，切换浅色或深色主题。'>
      <AppearanceForm />
    </ContentSection>
  )
}
