import { createContext, useContext, useState, ReactNode } from 'react'

interface AppState {
  companyId: number | null
  companyName: string | null
  activeSprintId: number | null
  setCompany: (id: number, name: string) => void
  setActiveSprint: (id: number) => void
}

const AppContext = createContext<AppState>({
  companyId: null,
  companyName: null,
  activeSprintId: null,
  setCompany: () => {},
  setActiveSprint: () => {},
})

export function AppProvider({ children }: { children: ReactNode }) {
  const [companyId, setCompanyId] = useState<number | null>(
    Number(localStorage.getItem('fabrique-company-id')) || null
  )
  const [companyName, setCompanyName] = useState<string | null>(
    localStorage.getItem('fabrique-company-name')
  )
  const [activeSprintId, setActiveSprintId] = useState<number | null>(
    Number(localStorage.getItem('fabrique-sprint-id')) || null
  )

  function setCompany(id: number, name: string) {
    setCompanyId(id)
    setCompanyName(name)
    localStorage.setItem('fabrique-company-id', String(id))
    localStorage.setItem('fabrique-company-name', name)
  }

  function setActiveSprint(id: number) {
    setActiveSprintId(id)
    localStorage.setItem('fabrique-sprint-id', String(id))
  }

  return (
    <AppContext.Provider value={{ companyId, companyName, activeSprintId, setCompany, setActiveSprint }}>
      {children}
    </AppContext.Provider>
  )
}

export const useApp = () => useContext(AppContext)
