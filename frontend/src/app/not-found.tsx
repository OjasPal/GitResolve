"use client";
import Link from "next/link";
import {useAuth} from "@/context/AuthContext";
export default function NotFound(){const {user}=useAuth();return <section className="system-page"><strong>404</strong><h1>Page not found.</h1><p>Unlike your bugs, this one isn’t ours to fix.</p><div className="system-actions"><Link className="button primary" href={user?"/dashboard":"/"}>{user?"Go to dashboard":"Go home"}</Link><button className="button" onClick={()=>window.history.back()}>Back</button></div></section>}
