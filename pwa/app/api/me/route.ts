import {NextResponse} from "next/server";import {pool} from "@/lib/db";import {session} from "@/lib/auth";
export async function GET(){const me=await session();if(me)return NextResponse.json({authenticated:true,person:me});const p=await pool.query("SELECT id,name FROM people WHERE active=true AND pin_hash IS NOT NULL ORDER BY name");return NextResponse.json({authenticated:false,people:p.rows})}
