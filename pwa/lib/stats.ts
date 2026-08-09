import "server-only";
import { pool } from "./db";

export type SeasonStats = {
  games_completed: number;
  umpire_completed: number;
  scorer_completed: number;
  upcoming_duties: number;
  total_assigned: number;
};

export async function getSeasonStats(personId: number): Promise<SeasonStats> {
  const result = await pool.query(
    "SELECT games_completed,umpire_completed,scorer_completed,upcoming_duties,total_assigned FROM season_workload WHERE person_id=$1",
    [personId],
  );
  const row = result.rows[0] || {};
  return {
    games_completed: Number(row.games_completed || 0),
    umpire_completed: Number(row.umpire_completed || 0),
    scorer_completed: Number(row.scorer_completed || 0),
    upcoming_duties: Number(row.upcoming_duties || 0),
    total_assigned: Number(row.total_assigned || 0),
  };
}
