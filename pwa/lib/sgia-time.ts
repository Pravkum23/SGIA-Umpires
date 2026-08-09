export const SGIA_TIMEZONE = "Asia/Singapore";

/** Serialize a PostgreSQL TIMESTAMP WITHOUT TIME ZONE as its SGIA wall-clock instant. */
export function singaporeTimestampSql(column: string) {
  if (!/^[a-z_][a-z0-9_.]*$/i.test(column)) throw new Error("Invalid timestamp column");
  return `to_char(${column}, 'YYYY-MM-DD\"T\"HH24:MI:SS') || '+08:00'`;
}

function fixtureDate(value: string) {
  return new Date(value);
}

export function formatFixtureDay(value: string) {
  return fixtureDate(value).toLocaleDateString("en-SG", { weekday: "long", timeZone: SGIA_TIMEZONE });
}

export function formatFixtureTime(value: string) {
  return fixtureDate(value).toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit", hour12: true, timeZone: SGIA_TIMEZONE });
}

export function formatFixtureDate(value: string) {
  return fixtureDate(value).toLocaleDateString("en-GB", { day: "numeric", month: "short", timeZone: SGIA_TIMEZONE });
}

export function formatFixtureDateTime(value: string) {
  return `${formatFixtureDay(value)} · ${formatFixtureTime(value)}`;
}
