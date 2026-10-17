export type AccountRole = 'OWNER' | 'MODERATOR' | 'CUSTOMER'

export function accountRoleLabel(role: string): string {
  return role === 'OWNER' ? 'Yönetici' : role === 'MODERATOR' ? 'Moderatör' : 'Kullanıcı'
}

export function authenticatedPath(role: string, path: string): string {
  const landing = role === 'OWNER' ? '/admin' : role === 'MODERATOR' ? '/moderator' : '/dashboard'
  if (['/login', '/register', '/forgot-password', '/reset-password', '/verify-email'].includes(path)) return landing
  // Moderators land on their panel after login but may open the normal site (/, /dashboard) like any member.
  if (role === 'MODERATOR' && path.startsWith('/admin')) return '/moderator'
  if (path.startsWith('/moderator') && role !== 'MODERATOR' && role !== 'OWNER') return landing
  return path
}
