"use client";
import Link from "next/link";
import {CheckCircle2,ArrowRight} from "lucide-react";
export default function Onboarding(){return <section className="connection-layout"><div className="glass-card facet connection-card"><CheckCircle2 size={40}/><span className="eyebrow mt-6">Connection ready</span><h1>Your repositories are connected.</h1><p>GitResolve can now investigate the repositories authorized through your GitHub App installation.</p><div className="connection-actions mt-8"><Link className="button primary" href="/repositories">Choose a repository<ArrowRight size={18}/></Link><Link className="button" href="/dashboard">Go to dashboard</Link></div></div></section>}
