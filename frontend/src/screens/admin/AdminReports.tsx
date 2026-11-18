// Reports (CSV + a print-friendly summary) and the audit journal.
import { useCallback, useEffect, useState } from 'react'
import { admin, type AuditData } from '../../lib/api'
import { Icon, type IconName } from '../../ui/icons'
import { Note, fmtDateTime, fmtNum } from '../auth/parts'
import { Loading, Problem, type OverviewState } from './AdminScreen'

type Kind = 'regions' | 'districts' | 'alerts'
const FILES: { kind: Kind; title: string; text: string; icon: IconName }[] = [
  { kind: 'regions', title: 'Hududlar', text: 'Viloyatlar kesimida homiladorlar, signallar va javob vaqti.', icon: 'building' },
  { kind: 'districts', title: 'Tumanlar', text: '199 tuman kesimida xavf guruhlari va ochiq signallar.', icon: 'map' },
  { kind: 'alerts', title: 'Signallar', text: 'Signallar soni: sabab, daraja va javob muddati boʻyicha.', icon: 'bell' },
]

function saveBlob(blob: Blob, name: string) {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url; a.download = name
  document.body.appendChild(a); a.click(); a.remove()
  setTimeout(() => URL.revokeObjectURL(url), 1500)
}
const pct = (v: number | null) => (v === null ? '—' : `${fmtNum(v, Number.isInteger(v) ? 0 : 1)}%`)
const num1 = (v: number | null) => (v === null ? '—' : fmtNum(v, Number.isInteger(v) ? 0 : 1))

export function AdminReports({ ov }: { ov: OverviewState }) {
  const [busy, setBusy] = useState<Kind | null>(null)
  const [msg, setMsg] = useState<{ kind: 'err' | 'ok'; text: string } | null>(null)
  const download = async (kind: Kind) => {
    if (busy) return
    setBusy(kind); setMsg(null)
    try {
      const blob = await admin.exportCsv(kind)
      const name = `onalik-${kind}-${new Date().toISOString().slice(0, 10)}.csv`
      saveBlob(blob, name)
      setMsg({ kind: 'ok', text: `${name} yuklab olindi.` })
    } catch (e) { setMsg({ kind: 'err', text: (e as Error).message }) } finally { setBusy(null) }
  }
  const d = ov.data
  const k = d?.kpis

  return (
    <>
      <div className="ad-files">
        {FILES.map((f) => (
          <section key={f.kind} className="ad-panel ob-glass ad-file">
            <span className="ad-file-ic"><Icon name={f.icon} size={22} /></span>
            <h2>{f.title}</h2>
            <p className="small muted grow">{f.text}</p>
            <button className="btn small soft" onClick={() => download(f.kind)} disabled={busy !== null}>
              <Icon name="download" size={16} />{busy === f.kind ? 'Tayyorlanmoqda…' : 'CSV yuklab olish'}
            </button>
          </section>
        ))}
      </div>
      {msg && <Note kind={msg.kind}>{msg.text}</Note>}

      <section className="ad-panel ob-glass">
        <header className="ad-panel-head">
          <div className="grow"><h2>Qisqa hisobot</h2><p className="small muted">Chop etish yoki PDF sifatida saqlash uchun · oxirgi {ov.days} kun</p></div>
          <button className="btn small" onClick={() => window.print()} disabled={!d}><Icon name="file" size={16} />Chop etish</button>
        </header>
        {!d || !k ? (ov.error ? <Problem message={ov.error} onRetry={ov.reload} /> : <Loading />) : (
          <article className="ad-print">
            <header className="ad-print-head">
              <h1>Tayanch — qisqa hisobot</h1>
              <p>{fmtDateTime(d.generated_at || new Date().toISOString())} holatiga · oxirgi {ov.days} kun · faqat jamlangan sonlar</p>
            </header>
            <dl className="ad-print-kpis">
              <div><dt>Homiladorlar</dt><dd className="num">{fmtNum(k.pregnant)}</dd></div>
              <div><dt>Chaqaloqlar</dt><dd className="num">{fmtNum(k.newborns)}</dd></div>
              <div><dt>Xavfli</dt><dd className="num">{fmtNum(k.red)}</dd></div>
              <div><dt>Eʼtibor</dt><dd className="num">{fmtNum(k.yellow)}</dd></div>
              <div><dt>Ochiq signallar</dt><dd className="num">{fmtNum(k.open_alerts)}</dd></div>
              <div><dt>Muddati oʻtgan</dt><dd className="num">{fmtNum(k.overdue_alerts)}</dd></div>
              <div><dt>Oʻrtacha javob, daq</dt><dd className="num">{num1(k.median_response_min)}</dd></div>
              <div><dt>30 daqiqada javob</dt><dd className="num">{pct(k.answered_in_sla_pct)}</dd></div>
            </dl>
            {d.by_region.length > 0 && (
              <table className="ad-print-table">
                <thead><tr><th>Hudud</th><th>Homilador</th><th>Xavfli</th><th>Eʼtibor</th><th>Ochiq</th><th>Kechikkan</th><th>Javob, daq</th><th>30 daq ichida</th></tr></thead>
                <tbody>
                  {d.by_region.map((r) => (
                    <tr key={r.soato}>
                      <th scope="row">{r.name}</th>
                      <td className="num">{fmtNum(r.pregnant)}</td><td className="num">{fmtNum(r.red)}</td><td className="num">{fmtNum(r.yellow)}</td>
                      <td className="num">{fmtNum(r.open_alerts)}</td><td className="num">{fmtNum(r.overdue_alerts)}</td>
                      <td className="num">{num1(r.median_response_min)}</td><td className="num">{pct(r.answered_in_sla_pct)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
            <p className="ad-print-foot">Demo: barcha bemorlar sunʼiy. Tizim tashxis qoʻymaydi — qarorni shifokor qabul qiladi.</p>
          </article>
        )}
      </section>
    </>
  )
}

export function AdminAudit() {
  const [data, setData] = useState<AuditData | null>(null)
  const [error, setError] = useState<string | null>(null)
  const load = useCallback(async () => {
    try { setData(await admin.audit(100)); setError(null) } catch (e) { setError((e as Error).message) }
  }, [])
  useEffect(() => { void load() }, [load])
  if (!data) return error ? <Problem message={error} onRetry={load} /> : <Loading />
  return (
    <section className="ad-panel ob-glass">
      <header className="ad-panel-head">
        <div className="grow"><h2>Oxirgi {data.items.length} ta yozuv</h2></div>
        <span className={`ob-status ${data.chain_ok ? 'green' : 'red'}`}><Icon name={data.chain_ok ? 'check' : 'alert'} size={14} />{data.chain_ok ? 'Zanjir butun' : 'Zanjir buzilgan'}</span>
        <button className="ob-icon-btn" onClick={load} aria-label="Yangilash" title="Yangilash"><Icon name="refresh" size={16} /></button>
      </header>
      {data.items.length === 0 ? <p className="muted center" style={{ padding: '24px 0' }}>Yozuvlar yoʻq.</p> : (
        <div className="ad-table-wrap flat">
          <table className="ad-table ad-audit">
            <thead><tr><th className="left">Vaqt</th><th className="left">Kim</th><th className="left">Harakat</th><th className="left">Obyekt</th></tr></thead>
            <tbody>
              {data.items.map((it, i) => (
                <tr key={`${it.at}-${i}`}>
                  <td className="left num muted">{fmtDateTime(it.at, false) || it.at}</td>
                  <td className="left">{it.actor}</td>
                  <td className="left ad-audit-act">{it.action}</td>
                  <td className="left muted">{it.resource}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  )
}
