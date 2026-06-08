export default function ZoneNode({ data }) {
  return (
    <div className="zone-node" style={{ '--accent': data.accent }}>
      <div className="zone-label">{data.label}</div>
    </div>
  )
}
