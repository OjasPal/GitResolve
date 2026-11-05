import Image from "next/image";
import Link from "next/link";
export function Brand({ compact = false, href = "/" }: { compact?: boolean; href?: string }) {
 return <Link href={href} className="brand" aria-label="GitResolve home"><Image src="/gitresolve-dashboard-image.png" width={44} height={44} alt="" priority className="brand-image" />{!compact && <span>GitResolve<span className="brand-period">.</span></span>}</Link>;
}
