"use client";
import {ReactLenis} from "lenis/react";
import {useReducedMotion} from "motion/react";
export function SmoothScroll({children}:{children:React.ReactNode}){
 const reduced=useReducedMotion();
 return reduced?children:<ReactLenis root options={{lerp:.12,smoothWheel:true,anchors:{offset:-96},allowNestedScroll:true}}>{children}</ReactLenis>;
}
