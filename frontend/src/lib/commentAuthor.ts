import type { TFunction } from 'i18next'

/** A board comment's author and, for a person's, who it was (issue #767). */
export interface CommentWho {
  author: string
  by?: string | null
  identified?: boolean
}

/**
 * The name shown for a comment. A person's comment (author `user`) shows the
 * name the signed-in session recorded; one made with no signed-in person (a
 * script, or a stack with authentication off) says so instead of passing for
 * someone's; an older comment that recorded nobody says the name was not
 * recorded. A role, a gate or the system shows as written.
 */
export function commentAuthor(comment: CommentWho, t: TFunction): string {
  if (comment.author !== 'user') return comment.author
  if (comment.identified === false) return t('commentAuthor.unidentified')
  return comment.by || t('commentAuthor.unrecorded')
}
