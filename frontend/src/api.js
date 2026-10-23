const BASE = import.meta.env.VITE_API_BASE || "http://localhost:8000"

export function getToken() {
    return localStorage.getItem("sopir_token")
}

export function setToken(token) {
    localStorage.setItem("sopir_token", token)
}

export function clearToken() {
    localStorage.removeItem("sopir_token")
}

async function request(path, options = {}) {
    const token = getToken();
    const headers = { ...(options.headers || {}) };
    if (token) headers["Authorization"] = `Bearer ${token}`;

    const res = await fetch(`${BASE}${path}`, { ...options, headers});
    
    if (res.status === 401 && token) {
        clearToken();
        window.location.reload();
    }
    if (!res.ok){
        const text = await res.text();
        throw new Error(`${res.status} ${res.statusText}: ${text}`)
    }
    return res.json();
}

export function login(username, password) {
    return fetch(`${BASE}/auth/login`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({username, password})
    }).then(async (res) => {
        if (!res.ok) throw new Error("Login failed");
        const data = await res.json();
        setToken(data.access_token);
        return data;
    });
}

export const api = {
    seasons: () => request("/seasons"),
  season: (id) => request(`/seasons/${id}`),
  runs: (id) => request(`/seasons/${id}/runs`),
  telemetry: (sid, rid, maxPoints = 1000) =>
    request(`/seasons/${sid}/runs/${rid}/telemetry?max_points=${maxPoints}`),
  frameUrl: (sid, rid, atTick) =>
    `${BASE}/seasons/${sid}/runs/${rid}/frame?at_tick=${atTick}`,
}