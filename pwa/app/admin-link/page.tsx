import Image from "next/image";

export default function AdminLink() {
  const url = process.env.NEXT_PUBLIC_ADMIN_URL;
  return <main className="shell"><div className="brand"><Image src="/icon-192.png" alt="SGIA" width={112} height={112} /><h1>SGIA Admin</h1></div>{url ? <a className="primary link" href={url}>OPEN SGIA ADMIN</a> : <p>Admin link is not configured.</p>}</main>;
}
