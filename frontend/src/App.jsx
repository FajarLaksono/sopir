import { useState } from "react";
import Login from "./Login";
import "./App.css"
import Dashboard from "./Dashboard";
import { getToken } from "./api";

export default function App() {
  const [loggedIn, setLoggedIn] = useState(Boolean(getToken()));

  if (!loggedIn) {
    return <Login onLogin={() => setLoggedIn(true)} />;
  }
  return <Dashboard />;
}
