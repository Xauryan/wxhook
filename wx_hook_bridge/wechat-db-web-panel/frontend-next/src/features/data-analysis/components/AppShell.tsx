export type ShellContext = {
  profile?: import('../lib/api').Profile
  profileResponse?: import('../lib/api').ProfileResponse
  profileName: string
  snapshotKey?: string
}
