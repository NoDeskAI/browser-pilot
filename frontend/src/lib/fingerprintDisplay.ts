export interface FingerprintClientHints {
  platform?: unknown
  platformVersion?: unknown
}

function asText(value: unknown): string {
  return typeof value === 'string' ? value.trim() : ''
}

export function osVersionLabel(clientHints?: FingerprintClientHints | null): string {
  const platform = asText(clientHints?.platform)
  const platformVersion = asText(clientHints?.platformVersion)

  if (!platform) return '-'

  if (platform === 'Windows') {
    const major = Number.parseInt(platformVersion.split('.')[0] || '', 10)
    if (Number.isFinite(major)) {
      if (major === 0) return 'Windows 7/8/8.1'
      if (major >= 13) return 'Windows 11'
      if (major >= 1 && major <= 10) return 'Windows 10'
    }
    return platform
  }

  return platformVersion ? `${platform} ${platformVersion}` : platform
}
