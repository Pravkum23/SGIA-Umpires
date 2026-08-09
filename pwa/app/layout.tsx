import type { Metadata, Viewport } from "next";import "./styles.css";import "./season.css";import { ServiceWorker } from "./service-worker";
export const metadata:Metadata={title:"SGIA Umpires",description:"SGIA volunteer availability",manifest:"/manifest.webmanifest",appleWebApp:{capable:true,statusBarStyle:"black-translucent",title:"SGIA Umpires"},icons:{apple:"/apple-touch-icon.png"}};
export const viewport:Viewport={themeColor:"#0f513f",width:"device-width",initialScale:1,viewportFit:"cover"};
export default function Layout({children}:{children:React.ReactNode}){return <html lang="en"><body>{children}<ServiceWorker/></body></html>}
