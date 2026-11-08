import type { ReactNode } from "react";

type ReadingSection = { title: string; text?: string; content?: ReactNode };

export function LegalPage({title,intro,sections,guide=false}:{
  title:string;
  intro:string;
  sections:ReadingSection[];
  guide?:boolean;
}) {
  return <article className={`legal-page${guide?" help-page":""}`}>
    <header>
      <span className="eyebrow">{guide?"GitResolve / Help & guidance":"GitResolve / Trust & transparency"}</span>
      <h1>{title}</h1>
      <p>{intro}</p>
      {!guide&&<div className="legal-draft">Draft for review. These policies are being finalized, including operator details, contact information and jurisdiction. This page does not claim legal certification.</div>}
    </header>
    <div className="legal-layout">
      <nav aria-label="On this page">{sections.map((section,index)=>
        <a href={`#section-${index}`} key={section.title}>{section.title}</a>
      )}</nav>
      <div>{sections.map((section,index)=>
        <section id={`section-${index}`} key={section.title}>
          <span className="eyebrow">{String(index+1).padStart(2,"0")}</span>
          <h2>{section.title}</h2>
          {section.text&&<p>{section.text}</p>}
          {section.content}
        </section>
      )}
      </div>
    </div>
  </article>;
}
