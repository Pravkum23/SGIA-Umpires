"use client";

import Image from "next/image";
import { useEffect, useState } from "react";
import { formatFixtureDateTime } from "@/lib/sgia-time";

type Person = { id: number; name: string; can_umpire?: boolean; can_score?: boolean };
type Fixture = { id: number; starts_at: string; home_team: string; away_team: string; votes: number; selected: boolean; initials: string };
type Duty = { fixture_id: number; role: string; status: string; starts_at: string; home_team: string; away_team: string };
type Poll = { title: string; range: string; state: "OPEN" | "FROZEN" | "PUBLISHED"; fixtures: Fixture[] };
type MeResponse = { authenticated: boolean; person?: Person; people?: Person[] };
type SeasonStats = { games_completed: number; umpire_completed: number; scorer_completed: number; upcoming_duties: number; total_assigned: number };

async function api<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, { ...init, headers: { "Content-Type": "application/json", ...(init?.headers || {}) } });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || "Request failed");
  return data;
}

export default function Home() {
  const [loading, setLoading] = useState(true);
  const [me, setMe] = useState<Person | null>(null);
  const [people, setPeople] = useState<Person[]>([]);
  const [personId, setPersonId] = useState("");
  const [pin, setPin] = useState("");
  const [error, setError] = useState("");
  const [tab, setTab] = useState("home");
  const [poll, setPoll] = useState<Poll | null>(null);
  const [duties, setDuties] = useState<Duty[]>([]);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [confirm, setConfirm] = useState<Duty | null>(null);
  const [saved, setSaved] = useState(false);
  const [stats, setStats] = useState<SeasonStats | null>(null);

  async function load() {
    setLoading(true);
    try {
      const identity = await api<MeResponse>("/api/me");
      if (identity.authenticated && identity.person) {
        setMe(identity.person);
        const [currentPoll, currentDuties, currentStats] = await Promise.all([
          api<Poll>("/api/poll"),
          api<{ duties: Duty[] }>("/api/duties"),
          api<{ stats: SeasonStats }>("/api/stats"),
        ]);
        setPoll(currentPoll);
        setSelected(new Set(currentPoll.fixtures.filter((fixture) => fixture.selected).map((fixture) => fixture.id)));
        setDuties(currentDuties.duties);
        setStats(currentStats.stats);
      } else {
        setPeople(identity.people || []);
      }
    } catch {
      setError("SGIA Umpires is temporarily unavailable. Please try again shortly.");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { void load(); }, []);

  async function login() {
    setError("");
    try {
      await api("/api/auth/login", { method: "POST", body: JSON.stringify({ personId: Number(personId), pin }) });
      await load();
    } catch (caught) {
      setError(caught instanceof Error && caught.message === "LOCKED" ? "Too many attempts. Try again in 15 minutes." : "Incorrect PIN.");
    }
  }

  async function logout() {
    await api("/api/auth/logout", { method: "POST" });
    setMe(null); setPoll(null); setDuties([]); setStats(null); setPersonId(""); setPin("");
    await load();
  }

  async function save() {
    await api("/api/availability", { method: "PUT", body: JSON.stringify({ fixtureIds: [...selected] }) });
    setSaved(true);
    await load();
  }

  async function withdraw() {
    if (!confirm) return;
    await api("/api/duties/withdraw", { method: "POST", body: JSON.stringify({ fixtureId: confirm.fixture_id, role: confirm.role }) });
    setConfirm(null);
    await load();
  }

  if (loading) return <main className="splash"><Image src="/icon-192.png" alt="SGIA" width={112} height={112} priority /><h1>SGIA Umpires</h1><p>Singapore Indian Association</p></main>;

  if (!me) return <main className="shell login">
    <div className="brand"><Image src="/icon-192.png" alt="SGIA" width={112} height={112} priority /><h1>Welcome to SGIA Umpires</h1><p>Singapore Indian Association</p></div>
    <label>Select your name<select value={personId} onChange={(event) => setPersonId(event.target.value)}><option value="">Select your name</option>{people.map((person) => <option key={person.id} value={person.id}>{person.name}</option>)}</select></label>
    <label>Enter your 4-digit PIN<input inputMode="numeric" type="password" maxLength={4} value={pin} onChange={(event) => setPin(event.target.value.replace(/\D/g, ""))} /></label>
    {error && <p className="error">{error}</p>}
    <button className="primary" disabled={!personId || pin.length !== 4} onClick={login}>CONTINUE</button>
  </main>;

  const showPoll = tab === "home" || tab === "availability";
  return <>
    <main className="shell">
      <div className="top"><Image src="/icon-192.png" alt="SGIA" width={52} height={52} /><div><b>SGIA Umpires</b><span>Hi {me.name}</span></div></div>
      {stats && <section className="season-card"><small>MY SEASON</small><div className="season-grid"><div><b>{stats.games_completed}</b><span>Completed</span></div><div><b>{stats.umpire_completed}</b><span>As Umpire</span></div><div><b>{stats.scorer_completed}</b><span>As Scorer</span></div><div><b>{stats.upcoming_duties}</b><span>Upcoming Duties</span></div></div>{stats.games_completed === 0 && <p>No completed duties yet this season.</p>}</section>}
      {showPoll && poll && <section>
        <div className="sectionTitle"><div><small>CURRENT AVAILABILITY</small><h2>{poll.title}</h2><p>{poll.range}</p></div><span className={`badge ${poll.state.toLowerCase()}`}>{poll.state}</span></div>
        {poll.fixtures.length ? poll.fixtures.map((fixture) => <button disabled={poll.state !== "OPEN"} className={`fixture ${selected.has(fixture.id) ? "chosen" : ""}`} key={fixture.id} onClick={() => setSelected((previous) => { const next = new Set(previous); if (next.has(fixture.id)) next.delete(fixture.id); else next.add(fixture.id); return next; })}>
          <i>{selected.has(fixture.id) ? "✓" : "○"}</i><span><b>{formatFixtureDateTime(fixture.starts_at)}</b><small>{fixture.home_team} vs {fixture.away_team}</small><em><u style={{ width: `${Math.min(100, fixture.votes * 10)}%` }} /> {fixture.initials} · {fixture.votes} available</em></span>
        </button>) : <div className="empty">No current availability poll.</div>}
        {poll.state === "OPEN" && <button className="primary" onClick={save}>{poll.fixtures.some((fixture) => fixture.selected) ? "UPDATE MY AVAILABILITY" : "SAVE MY AVAILABILITY"}</button>}
        {saved && <p className="success">✓ Availability updated</p>}
        {poll.state === "FROZEN" && <div className="notice"><b>Availability closed</b><br />Allocations are being prepared.</div>}
        <details><summary>View votes</summary>{poll.fixtures.map((fixture) => <p key={fixture.id}>{fixture.home_team} vs {fixture.away_team}: {fixture.votes}</p>)}</details>
      </section>}
      {(tab === "home" || tab === "duties") && <section><h2>MY DUTIES</h2>{duties.length ? duties.map((duty) => <article className="duty" key={duty.fixture_id + duty.role}><b>{formatFixtureDateTime(duty.starts_at)}</b><p>{duty.home_team} vs {duty.away_team}</p><strong>{duty.role.replace("_", " ").toUpperCase()}</strong><span>{duty.status.replace("_", " ")}</span>{duty.status === "ASSIGNED" && <button onClick={() => setConfirm(duty)}>I CAN&apos;T ATTEND</button>}</article>) : <div className="empty">No published duties.</div>}</section>}
      {tab === "profile" && <section><h2>PROFILE</h2><div className="profile"><b>{me.name}</b><p>{me.can_umpire ? "Umpire " : ""}{me.can_score ? "Scorer" : ""}</p></div><button className="secondary" onClick={logout}>Not {me.name}? Change person</button></section>}
    </main>
    <nav>{[["home", "⌂", "Home"], ["availability", "✓", "Availability"], ["duties", "▷", "My Duties"], ["profile", "●", "Profile"]].map(([id, icon, label]) => <button className={tab === id ? "active" : ""} key={id} onClick={() => setTab(id)}><i>{icon}</i>{label}</button>)}</nav>
    {confirm && <div className="modal"><div><h3>Are you sure you can&apos;t attend this duty?</h3><p>{confirm.home_team} vs {confirm.away_team}</p><button className="secondary" onClick={() => setConfirm(null)}>Cancel</button><button className="danger" onClick={withdraw}>Confirm withdrawal</button></div></div>}
  </>;
}
