import { NextResponse } from "next/server";
import { session } from "@/lib/auth";
import { getSeasonStats } from "@/lib/stats";

export async function GET() {
  const person = await session();
  if (!person) return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  return NextResponse.json({ stats: await getSeasonStats(person.id) });
}
