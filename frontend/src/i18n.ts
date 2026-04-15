import { createI18n } from 'vue-i18n'
import zh from './locales/zh'
import en from './locales/en'

const STORAGE_KEY = 'bpilot-ui-locale'

function detectLocale(): 'zh' | 'en' {
  const saved = localStorage.getItem(STORAGE_KEY)
  if (saved === 'zh' || saved === 'en') return saved
  return navigator.language.startsWith('zh') ? 'zh' : 'en'
}

const locale = detectLocale()

const i18n = createI18n({
  legacy: false,
  locale,
  fallbackLocale: 'en',
  messages: { zh, en },
})

export function setLocale(lang: 'zh' | 'en') {
  ;(i18n.global.locale as any).value = lang
  localStorage.setItem(STORAGE_KEY, lang)
  document.documentElement.lang = lang === 'zh' ? 'zh-CN' : 'en'
}

export function getLocale(): 'zh' | 'en' {
  return (i18n.global.locale as any).value
}

document.documentElement.lang = locale === 'zh' ? 'zh-CN' : 'en'

export default i18n
