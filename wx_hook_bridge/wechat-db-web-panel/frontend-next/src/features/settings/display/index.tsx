import { ContentSection } from '../components/content-section'
import { DisplayForm } from './display-form'

export function SettingsDisplay() {
  return (
    <ContentSection title='显示' desc='开启或关闭各项内容，调整界面显示。'>
      <DisplayForm />
    </ContentSection>
  )
}
