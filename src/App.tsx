import { useEffect } from 'react'
import { AppLayout } from '@/components/layout/AppLayout'
import { SetupWizard } from '@/components/wizard/SetupWizard'
import { useAppStore } from '@/store/appStore'
import { applyTheme } from '@/themes'
import { useAppearanceApply } from '@/lib/appearance'

export default function App() {
  const { settings, wizardCompleted } = useAppStore()

  // Sync appearance preferences (font size, motion, contrast, etc.) onto
  // <html> data-attributes + CSS variables. Runs even during the wizard so
  // font-size / high-contrast preferences apply immediately.
  useAppearanceApply()

  useEffect(() => {
    applyTheme(settings.theme)
  }, [settings.theme])

  if (!wizardCompleted) {
    return <SetupWizard />
  }

  return <AppLayout />
}
