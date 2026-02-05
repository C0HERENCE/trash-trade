let cachedBasePath

export const getBasePath = () => {
  if (cachedBasePath !== undefined) return cachedBasePath
  let path = window.location.pathname || ''
  if (!path || path === '/') {
    cachedBasePath = ''
    return cachedBasePath
  }
  if (path.length > 1 && path.endsWith('/')) {
    path = path.slice(0, -1)
  }
  const strategyIdx = path.indexOf('/strategy/')
  if (strategyIdx >= 0) {
    cachedBasePath = path.slice(0, strategyIdx)
    return cachedBasePath
  }
  if (path.endsWith('/dashboard')) {
    cachedBasePath = path.slice(0, -'/dashboard'.length)
    return cachedBasePath
  }
  cachedBasePath = path
  return cachedBasePath
}
