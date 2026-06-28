import { useEffect } from 'react'
import { AppLayout } from '@/components/layout/AppLayout'
import { SetupWizard } from '@/components/wizard/SetupWizard'
import { useAppStore } from '@/store/appStore'
import { applyTheme } from '@/themes'

export default function App() {
  const { settings, wizardCompleted } = useAppStore()

  useEffect(() => {
    applyTheme(settings.theme)
  }, [settings.theme])

  if (!wizardCompleted) {
    return <SetupWizard />
  }

  return <AppLayout />
}
