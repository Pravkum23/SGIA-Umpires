import "server-only";
import { pool, transaction } from "./db";
import { formatFixtureDate, formatFixtureDay, singaporeTimestampSql } from "./sgia-time";

export function pollTitle(rows: { starts_at: string }[]) {
  if (!rows.length) return { title: "Current Availability", range: "" };
  const dates = rows.map((row) => row.starts_at);
  const weekend = dates.every((date) => ["Saturday", "Sunday"].includes(formatFixtureDay(date)));
  return {
    title: weekend ? "Weekend Availability" : "Weekday Availability",
    range: dates.length === 1 ? formatFixtureDate(dates[0]) : `${formatFixtureDate(dates[0])}–${formatFixtureDate(dates.at(-1)!)}`,
  };
}

export async function getPoll(personId: number) {
  const state = await pool.query("SELECT poll_state FROM fixtures WHERE poll_state IN ('OPEN','FROZEN') GROUP BY poll_state ORDER BY CASE poll_state WHEN 'OPEN' THEN 1 ELSE 2 END LIMIT 1");
  const pollState = state.rows[0]?.poll_state;
  if (!pollState) return { state: "PUBLISHED", ...pollTitle([]), fixtures: [] };
  const startsAt = singaporeTimestampSql("f.starts_at");
  const result = await pool.query(`SELECT f.id,${startsAt} starts_at,f.home_team,f.away_team,COUNT(a.person_id)::int votes,BOOL_OR(a.person_id=$1) selected,COALESCE(STRING_AGG(SUBSTRING(p.name,1,1),'' ORDER BY p.name),'') initials FROM fixtures f LEFT JOIN availability a ON a.fixture_id=f.id LEFT JOIN people p ON p.id=a.person_id WHERE f.poll_state=$2 GROUP BY f.id ORDER BY f.starts_at`, [personId, pollState]);
  return { state: pollState, ...pollTitle(result.rows), fixtures: result.rows };
}

export async function saveAvailability(personId: number, fixtureIds: number[]) {
  return transaction(async (db) => {
    const open = await db.query("SELECT id FROM fixtures WHERE poll_state='OPEN'");
    const allowed = new Set<number>(open.rows.map((row) => row.id));
    const wanted = new Set(fixtureIds.filter((id) => allowed.has(id)));
    const old = await db.query("SELECT a.fixture_id FROM availability a JOIN fixtures f ON f.id=a.fixture_id WHERE a.person_id=$1 AND f.poll_state='OPEN'", [personId]);
    const before = new Set<number>(old.rows.map((row) => row.fixture_id));
    await db.query("DELETE FROM availability WHERE person_id=$1 AND fixture_id IN (SELECT id FROM fixtures WHERE poll_state='OPEN')", [personId]);
    for (const id of wanted) {
      await db.query("INSERT INTO availability(person_id,fixture_id) VALUES($1,$2) ON CONFLICT DO NOTHING", [personId, id]);
      if (!before.has(id)) await db.query("INSERT INTO availability_events(person_id,fixture_id,event_type) VALUES($1,$2,'ADDED')", [personId, id]);
    }
    for (const id of before) {
      if (!wanted.has(id)) await db.query("INSERT INTO availability_events(person_id,fixture_id,event_type) VALUES($1,$2,'REMOVED')", [personId, id]);
    }
    return [...wanted];
  });
}
