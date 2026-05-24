import type { ActiveSessionLease } from '../types'

type Translate = (key: string, named?: Record<string, unknown>) => string

function subjectSuffix(value: string, prefix: string): string {
  const raw = value.startsWith(prefix) ? value.slice(prefix.length) : value
  return raw.length > 8 ? raw.slice(0, 8) : raw
}

export function formatSessionLeaseOperator(lease: ActiveSessionLease | null | undefined, t: Translate): string {
  if (!lease) return ''
  const operator = lease.currentOperator || ''
  const name = lease.operatorName?.trim()
  if (lease.operatorType === 'api_token' || operator.startsWith('token:')) {
    return name
      ? t('sessionLease.apiTokenNamed', { name })
      : t('sessionLease.apiToken', { id: subjectSuffix(operator, 'token:') })
  }
  if (lease.operatorType === 'user' || operator.startsWith('user:')) {
    return name
      ? t('sessionLease.userNamed', { name })
      : t('sessionLease.user', { id: subjectSuffix(operator, 'user:') })
  }
  if (lease.operatorType === 'runtime_file_capture' || operator.startsWith('runtime:file_capture:')) {
    return t('sessionLease.fileCaptureAgent')
  }
  if (lease.operatorType === 'system' || operator.startsWith('system:')) {
    return t('sessionLease.system')
  }
  return operator || t('sessionLease.unknownOperator')
}
