// Every saved version of the clone: who made it, when, and why. An editor can bring an old one back as the newest.
import { Empty, ErrorNote, Loading, Modal } from '../components/ui'
import { fmt, useApi } from '../lib/api'

export function HistoryDialog({ cloneId, canRestore, busy, onRestore, onClose }) {
  const versions = useApi(`/api/clones/${cloneId}/versions`)
  const rows = [...(versions.data?.versions || [])].reverse()
  const latest = rows[0]?.number
  return (
    <Modal title="Version history" onClose={onClose} wide footer={<button className="btn primary" onClick={onClose}>Close</button>}>
      {versions.loading && !versions.data && <Loading rows={4} />}
      {versions.error && <ErrorNote error={versions.error} retry={versions.reload} />}
      {versions.data && rows.length === 0 && <Empty title="No versions yet">Every change you save becomes a version.</Empty>}
      {rows.length > 0 && (
        <table className="t history-table">
          <thead><tr><th>Version</th><th>Who</th><th>Why</th><th>When</th>{canRestore && <th><span className="hidden-text">Restore</span></th>}</tr></thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.number}>
                <td className="num">{row.number}{row.number === latest ? ' (latest)' : ''}</td>
                <td>{row.author}</td>
                <td>{row.reason}</td>
                <td>{row.created_at ? <time dateTime={row.created_at}>{fmt.ago(row.created_at)}</time> : ''}</td>
                {canRestore && (
                  <td>{row.number !== latest && <button className="btn small" disabled={busy} onClick={() => onRestore(row.number)}>Restore as newest</button>}</td>
                )}
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Modal>
  )
}
