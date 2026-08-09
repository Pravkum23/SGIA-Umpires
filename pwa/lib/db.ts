import "server-only";import {Pool,PoolClient} from "pg";
if(!process.env.DATABASE_URL) throw new Error("DATABASE_URL is required");
export const pool=new Pool({connectionString:process.env.DATABASE_URL,ssl:process.env.NODE_ENV==="production"?{rejectUnauthorized:false}:undefined,max:5});
export type DB=Pick<Pool|PoolClient,"query">;
export async function transaction<T>(fn:(db:PoolClient)=>Promise<T>){const c=await pool.connect();try{await c.query("BEGIN");const out=await fn(c);await c.query("COMMIT");return out}catch(e){await c.query("ROLLBACK");throw e}finally{c.release()}}
