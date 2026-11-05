"use client";
import Image from "next/image";
import Link from "next/link";
import {useRouter} from "next/navigation";
import {motion, useReducedMotion} from "motion/react";
import {ArrowLeft, ArrowUpRight} from "lucide-react";
import {useAuth} from "@/context/AuthContext";
export default function NotFound(){
 const {user}=useAuth();const reduced=useReducedMotion();const router=useRouter();
 return <section className="lost-page">
  <div className="lost-art" aria-hidden="true"><span>404</span><motion.div initial={false} animate={reduced?{}:{y:[0,-8,0]}} transition={{duration:5,repeat:Infinity,ease:"easeInOut"}}><Image src="/gitresolve-lost.png" width={380} height={380} sizes="(max-width: 600px) 240px, 380px" alt="" priority/></motion.div></div>
  <p className="eyebrow">A small detour.</p><h1>Even our agent<br/>gets a little lost.</h1>
  <p>This page has moved, or the address isn’t quite right.<br/>Let’s get you back to familiar ground.</p>
  <div className="system-actions"><Link className="button primary" href={user?"/dashboard":"/"}>{user?"Back to workspace":"Back to home"}<ArrowUpRight size={18}/></Link><button className="button" onClick={()=>{if(window.history.length>1)router.back();else router.push("/")}}><ArrowLeft size={18}/>Previous page</button></div>
 </section>
}
