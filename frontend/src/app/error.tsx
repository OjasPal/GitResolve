"use client";
import Link from "next/link";
export default function ErrorPage({reset}:{error:Error&{digest?:string};reset:()=>void}){return <section className="system-page"><strong>500</strong><h1>Something broke on our side.</h1><p>Try again. If the problem persists, come back in a moment.</p><div className="system-actions"><button className="button primary" onClick={reset}>Try again</button><Link className="button" href="/dashboard">Go to dashboard</Link></div></section>}
