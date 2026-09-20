import React,{useCallback,useEffect,useLayoutEffect,useRef,useState} from 'react';
import {Info} from 'lucide-react';

// Small (i) button. The explanation stays hidden until it is pressed, so the
// panels keep only the numbers and the decisions in view.
export default function InfoTip({label='자세한 설명',children,corner=false,size=13,className=''}) {
  const [open,setOpen]=useState(false),[box,setBox]=useState(null);
  const root=useRef(),button=useRef(),pop=useRef();
  const place=useCallback(()=>{
    if(!button.current||!pop.current)return;
    const r=button.current.getBoundingClientRect(),width=Math.min(300,window.innerWidth-24),height=pop.current.offsetHeight;
    // Open toward the wider side of the screen, then keep the box inside the viewport.
    const preferred=r.left+r.width/2<window.innerWidth/2?r.left:r.right-width;
    const left=Math.max(12,Math.min(preferred,window.innerWidth-width-12));
    let top=r.bottom+8;
    if(top+height>window.innerHeight-12)top=Math.max(12,r.top-height-8);
    setBox({top,left,width});
  },[]);
  useEffect(()=>{
    if(!open)return;
    const close=e=>{if(!root.current?.contains(e.target))setOpen(false);};
    const key=e=>{if(e.key==='Escape'){e.preventDefault();e.stopPropagation();setOpen(false);}};
    document.addEventListener('pointerdown',close);document.addEventListener('keydown',key,true);
    window.addEventListener('resize',place);document.addEventListener('scroll',place,true);
    return()=>{document.removeEventListener('pointerdown',close);document.removeEventListener('keydown',key,true);window.removeEventListener('resize',place);document.removeEventListener('scroll',place,true);};
  },[open,place]);
  useLayoutEffect(()=>{if(open)place();else setBox(null);},[open,children,place]);
  return <span className={'info-tip'+(corner?' corner':'')+(className?' '+className:'')} ref={root}>
    <button type="button" ref={button} className={'info-tip-button'+(open?' open':'')} aria-label={label} title={label} aria-expanded={open}
      onClick={e=>{e.preventDefault();e.stopPropagation();setOpen(v=>!v);}}><Info size={size}/></button>
    {open&&<div ref={pop} className="info-tip-pop" role="note" style={box||{visibility:'hidden',top:0,left:0,width:Math.min(300,window.innerWidth-24)}} onClick={e=>{e.stopPropagation();}}>{children}</div>}
  </span>;
}
