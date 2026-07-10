import Link from "next/link";

export default function NotFound() {
  return (
    <section className="section-panel empty-state">
      <h1>요청한 화면을 찾을 수 없습니다</h1>
      <Link href="/" className="text-link">대시보드로 이동</Link>
    </section>
  );
}
