import {Suspense} from "react";
import {AuthScreen} from "@/components/AuthScreen";
export default function Page(){return <Suspense fallback={<div className="page-loading">Opening sign-in…</div>}><AuthScreen mode="register"/></Suspense>}
