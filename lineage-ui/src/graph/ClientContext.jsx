import { createContext, useContext } from "react";

// Whichever backing is active — the served API, or a graph.json held in this
// tab. Components ask for data through this rather than knowing which.
const ClientContext = createContext(null);

export function ClientProvider({ client, children }) {
  return <ClientContext.Provider value={client}>{children}</ClientContext.Provider>;
}

export function useClient() {
  const client = useContext(ClientContext);
  if (!client) throw new Error("useClient must be used inside a ClientProvider");
  return client;
}
