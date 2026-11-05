"use client";
import {useState} from "react";
import {AnimatePresence,motion,useMotionValue,useReducedMotion,useSpring} from "motion/react";
import {ArrowUpRight,FileCheck2,Fingerprint,GitCommitHorizontal,ShieldCheck} from "lucide-react";
import Link from "next/link";
const layers=[
 {title:"A pinned starting point.",label:"Source",icon:GitCommitHorizontal,copy:"A specific repository revision anchors the investigation. Review the fix against the code it was actually tested on.",detail:"One revision. A traceable baseline."},
 {title:"Evidence you can inspect.",label:"Proof",icon:FileCheck2,copy:"A regression must fail before the fix and pass after it. The plan, patch and test output stay together in your repair workspace.",detail:"Before, after, and the evidence between."},
 {title:"Your approval has a fingerprint.",label:"Permission",icon:Fingerprint,copy:"Your decision is bound to the exact patch you reviewed. A changed fix needs new verification and a fresh approval before publication.",detail:"Different patch? Different approval."}
];
export function EvidenceLens({signedIn}:{signedIn:boolean}){
 const[index,setIndex]=useState(0),reduced=useReducedMotion();const x=useMotionValue(0),y=useMotionValue(0);
 const rx=useSpring(y,{stiffness:130,damping:24}),ry=useSpring(x,{stiffness:130,damping:24});
 const layer=layers[index];const Icon=layer.icon;
 return <section id="proof" className="evidence-section"><div className="landing-section evidence-layout"><div><span className="eyebrow">02 / Every fix carries its proof</span><h2>No black box.<br/>No crossed fingers.</h2><p>A patch is only part of the story. Explore the three things that make a GitResolve repair reviewable.</p><div className="evidence-tabs" role="tablist" aria-label="Explore repair evidence">{layers.map((l,i)=><button id={`proof-tab-${i}`} aria-controls="proof-panel" role="tab" aria-selected={index===i} tabIndex={index===i?0:-1} onKeyDown={e=>{if(e.key==="ArrowRight"||e.key==="ArrowLeft"){e.preventDefault();const next=(index+(e.key==="ArrowRight"?1:2))%3;setIndex(next);document.getElementById(`proof-tab-${next}`)?.focus()}}} key={l.label} onClick={()=>setIndex(i)}><l.icon size={17}/>{l.label}</button>)}</div><Link href={signedIn?"/tasks":"/login"} className="button">{signedIn?"Explore your repairs":"Sign in"}<ArrowUpRight size={16}/></Link></div>
 <div className="evidence-stage" onPointerMove={e=>{if(reduced||e.pointerType!=="mouse")return;const r=e.currentTarget.getBoundingClientRect();x.set(((e.clientX-r.left)/r.width-.5)*8);y.set(-((e.clientY-r.top)/r.height-.5)*6)}} onPointerLeave={()=>{x.set(0);y.set(0)}}>
 <motion.div className="evidence-object" style={reduced?undefined:{rotateX:rx,rotateY:ry}}><div className="evidence-orbit" aria-hidden="true"/><div className="evidence-seal" aria-hidden="true"><ShieldCheck size={44}/></div><div className="evidence-topline"><span>THE CHANGE CAPSULE</span><span>0{index+1} / 03</span></div><AnimatePresence mode="wait"><motion.div id="proof-panel" role="tabpanel" aria-labelledby={`proof-tab-${index}`} key={index} initial={{opacity:0,y:reduced?0:12}} animate={{opacity:1,y:0}} exit={{opacity:0,y:reduced?0:-8}} transition={{duration:.2}} className="evidence-reading"><Icon size={26}/><h3>{layer.title}</h3><p>{layer.copy}</p><div><i/>{layer.detail}</div></motion.div></AnimatePresence><div className="evidence-bottomline"><span>Inspectable by design</span><span>Controlled by you</span></div></motion.div></div></div></section>
}
