"use client";
import {useEffect, useRef, useState} from "react";
import Link from "next/link";
import {AnimatePresence, motion, useReducedMotion} from "motion/react";
import {ArrowUp, Check, ChevronDown, MessageSquare, Pencil, Sparkles} from "lucide-react";
import {TaskResponse} from "@/types";
import {gateConversation} from "@/lib/api";

export function GateConversation({task,onSave,onEditingChange}:{task:TaskResponse;onSave:(task:TaskResponse)=>void;onEditingChange:(value:boolean)=>void}){
 const [message,setMessage]=useState("");
 const [operation,setOperation]=useState<"ask"|"revise">("ask");
 const [busy,setBusy]=useState(false),[error,setError]=useState("");
 const [editing,setEditing]=useState(false),[warning,setWarning]=useState(false);
 const [prompt,setPrompt]=useState(task.issue.body||"");
 const log=useRef<HTMLDivElement>(null);
 const retry=useRef<{key:string;id:string}|null>(null);
 const reduced=useReducedMotion();
 useEffect(()=>{log.current?.scrollTo({top:log.current.scrollHeight,behavior:reduced?"instant":"smooth"})},[task.gateMessages?.length,reduced]);
 const first=task.status==="awaiting_start";
 if(!first&&task.status!=="awaiting_approval")return null;
 const pending=busy||Boolean(task.gateChatBusy);
 async function send(op:"ask"|"revise"|"prompt",text:string){
  if(!text.trim()||pending)return;
  setBusy(true);setError("");onEditingChange(true);
  try{
   const key=JSON.stringify([task.id,task.gateRevision||0,op,text.trim()]);
   if(retry.current?.key!==key)retry.current={key,id:crypto.randomUUID()};
   const updated=await gateConversation(task.id,{message:text.trim(),operation:op,revision:task.gateRevision||0,requestId:retry.current.id});
   retry.current=null;
   setMessage("");setEditing(false);onEditingChange(false);onSave(updated);
  }catch(e){setError(e instanceof Error?e.message:"Your message couldn’t be sent. Please try again.");onEditingChange(editing)}
  finally{setBusy(false)}
 }
 return <motion.section className="gate-conversation" initial={{opacity:0,y:10}} animate={{opacity:1,y:0}} transition={{duration:.35}} aria-label="Review conversation">
  <header><div className="chat-diamond"><MessageSquare size={20}/></div><div><span className="eyebrow">{first?"Plan review / Gate 01":"Fix review / Gate 02"}</span><h2>A second look, together.</h2><p>{first?"Ask about the plan, or tell the agent what to change.":"Explore the evidence, or ask for a refinement before publishing."}</p></div><span className="chat-paused"><i/>Awaiting your decision</span></header>
  <div className="gate-chat-body" ref={log} data-lenis-prevent role="log" aria-label="Conversation messages" aria-live="polite" aria-relevant="additions">
   {!task.gateMessages?.length&&<div className="chat-welcome"><Sparkles size={24}/><p>{first?"A good plan starts with a conversation.":"The fix is ready for your questions."}</p><span>{first?"Clarify the approach before the agent changes code.":"Replies use the recorded plan, patch and test evidence."}</span></div>}
   <AnimatePresence initial={false}>{task.gateMessages?.map((m,i)=><motion.article key={i} className={`chat-message ${m.role}`} initial={{opacity:0,y:8}} animate={{opacity:1,y:0}}><span>{m.role==="user"?"You":"GitResolve"}</span><p>{m.content}</p></motion.article>)}</AnimatePresence>
   {pending&&<div className="chat-thinking" role="status"><span/><span/><span/>Preparing your reply</div>}
  </div>
  <form className="gate-composer" onSubmit={e=>{e.preventDefault();void send(operation,message)}}>
   <div className="chat-intent" role="group" aria-label="Message purpose">{([['ask','Ask a question'],['revise','Request changes']] as const).map(([value,label])=><button type="button" key={value} aria-pressed={operation===value} disabled={pending} onClick={()=>setOperation(value)}>{label}</button>)}</div>
   <label className="sr-only" htmlFor={`gate-message-${task.id}`}>Message to GitResolve</label>
   <textarea id={`gate-message-${task.id}`} aria-describedby={error?`gate-error-${task.id}`:undefined} aria-invalid={Boolean(error)} value={message} onChange={e=>setMessage(e.target.value)} maxLength={4000} rows={3} placeholder={operation==="ask"?"What would you like to understand?":"Tell the agent what should change…"} disabled={pending||editing}/>
   <div className="chat-composer-footer"><span>{operation==="revise"?first?"A new plan will return for your approval.":"The fix will be tested again. A new approval is required.":"Answers are grounded in this run’s evidence."}</span><button className="chat-send" aria-label={operation==="ask"?"Send question":"Request changes"} disabled={!message.trim()||pending||editing}><ArrowUp size={20}/></button></div>
  </form>
  {error&&<p className="inline-error" id={`gate-error-${task.id}`} role="alert">{error}</p>}
  <button className="chat-edit-request" disabled={pending} aria-expanded={editing||warning} onClick={()=>{if(first){setEditing(v=>!v);onEditingChange(!editing)}else setWarning(v=>!v)}}><Pencil size={14}/>Edit original request<ChevronDown size={14}/></button>
  <AnimatePresence>{editing&&first&&<motion.div className="chat-request-editor" initial={{height:0,opacity:0}} animate={{height:"auto",opacity:1}} exit={{height:0,opacity:0}}><label>Your original request<textarea value={prompt} onChange={e=>setPrompt(e.target.value)} maxLength={20000} rows={6} disabled={pending}/></label><p>The agent will investigate your updated request and prepare a new plan. Repair stays paused until you approve it.</p><button className="button primary" disabled={!prompt.trim()||pending} onClick={()=>void send("prompt",prompt)}><Check size={16}/>Save & investigate again</button></motion.div>}
  {warning&&!first&&<motion.div className="chat-restart-warning" role="alert" initial={{opacity:0,y:-5}} animate={{opacity:1,y:0}}><strong>Changing the original request requires a new workflow.</strong><p>The current fix and its approvals won’t carry over. To refine this fix, use Request changes above.</p><Link className="button" href={`/tasks/new?repo=${encodeURIComponent(task.repoId)}`}>Start a new repair</Link></motion.div>}</AnimatePresence>
 </motion.section>
}
