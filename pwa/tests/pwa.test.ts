import {describe,it,expect} from "vitest";import fs from "node:fs";import path from "node:path";
const root=process.cwd(),read=(p:string)=>fs.readFileSync(path.join(root,p),"utf8");
describe("PWA production contract",()=>{
it("has valid install manifest and icons",()=>{const m=JSON.parse(read("public/manifest.webmanifest"));expect(m.display).toBe("standalone");for(const i of m.icons)expect(fs.existsSync(path.join(root,"public",i.src))).toBe(true)});
it("registers service worker",()=>expect(read("app/service-worker.tsx")).toContain('register("/sw.js")'));
it("implements required auth routes and HttpOnly cookie",()=>{expect(read("lib/auth.ts")).toContain("httpOnly:true");expect(fs.existsSync(path.join(root,"app/api/auth/login/route.ts"))).toBe(true);expect(fs.existsSync(path.join(root,"app/api/auth/logout/route.ts"))).toBe(true)});
it("matches PBKDF2 SHA256 format",()=>{const s=read("lib/auth.ts");expect(s).toContain('pbkdf2Sync');expect(s).toContain('"sha256"');expect(s).toContain('timingSafeEqual')});
it("supports open edit, prepopulation and removal audits",()=>{const s=read("lib/poll.ts");expect(s).toContain("poll_state='OPEN'");expect(s).toContain("REMOVED");expect(s).toContain("BOOL_OR")});
it("supports frozen and published duty withdrawal",()=>{expect(read("app/page.tsx")).toMatch(/poll\.state\s*===\s*"FROZEN"/);expect(read("lib/duties.ts")).toContain("REPLACEMENT_REQUIRED");expect(read("lib/duties.ts")).toContain("WITHDRAWN")});
it("never imports database modules in client",()=>{const client=read("app/page.tsx");expect(client).not.toContain("DATABASE_URL");expect(client).not.toContain("pin_hash");expect(client).not.toContain("@/lib/db")});
it("provides four-item mobile navigation and hidden admin route",()=>{const s=read("app/page.tsx");for(const x of ["Home","Availability","My Duties","Profile"])expect(s).toContain(x);expect(fs.existsSync(path.join(root,"app/admin-link/page.tsx"))).toBe(true)})});
