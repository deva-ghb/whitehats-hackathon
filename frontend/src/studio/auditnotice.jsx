import { useEffect } from 'react'
import { useApi } from '../lib/api'
import { useToast } from '../components/ui'

const FRESH_MS = 3 * 60 * 1000

/** AUD.6: after a person connects AWS or GitHub, what WhiteHats decided about a full audit, said once: started, because
 * something new is connected, or skipped, because the same connections were already audited. Renders nothing. */
export function AuditNotice() {
  const { data } = useApi('/api/audits', { poll: 15_000 })
  const toast = useToast()
  useEffect(() => {
    const newest = data?.audits?.find((a) => a.trigger === 'connections' || a.trigger === 'onboarding')
    if (!newest || Date.now() - Date.parse(`${newest.created_at}Z`) > FRESH_MS) return
    const key = `wh.audit-notice.${newest.id}`
    try {
      if (sessionStorage.getItem(key) === '1') return
      sessionStorage.setItem(key, '1')
    } catch { /* a private window: said again on a reload at worst */ }
    toast(newest.status === 'skipped' ? newest.trigger_detail : `A full security audit started. ${newest.trigger_detail}`)
  }, [data, toast])
  return null
}
