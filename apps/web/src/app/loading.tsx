export default function Loading() {
  return (
    <div aria-label="불러오는 중" className="section-panel panel-body">
      <div className="loading-line" style={{ width: "34%", height: 24 }} />
      <div className="loading-line" style={{ width: "70%", marginTop: 14 }} />
      <div className="loading-line" style={{ width: "100%", height: 220, marginTop: 28 }} />
    </div>
  );
}
