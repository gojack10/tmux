/* $OpenBSD$ */

/*
 * Copyright (c) 2026 Jack Gebert
 *
 * Permission to use, copy, modify, and distribute this software for any
 * purpose with or without fee is hereby granted, provided that the above
 * copyright notice and this permission notice appear in all copies.
 *
 * THE SOFTWARE IS PROVIDED "AS IS" AND THE AUTHOR DISCLAIMS ALL WARRANTIES
 * WITH REGARD TO THIS SOFTWARE INCLUDING ALL IMPLIED WARRANTIES OF
 * MERCHANTABILITY AND FITNESS. IN NO EVENT SHALL THE AUTHOR BE LIABLE FOR
 * ANY SPECIAL, DIRECT, INDIRECT, OR CONSEQUENTIAL DAMAGES OR ANY DAMAGES
 * WHATSOEVER RESULTING FROM LOSS OF MIND, USE, DATA OR PROFITS, WHETHER
 * IN AN ACTION OF CONTRACT, NEGLIGENCE OR OTHER TORTIOUS ACTION, ARISING
 * OUT OF OR IN CONNECTION WITH THE USE OR PERFORMANCE OF THIS SOFTWARE.
 */

#include <sys/types.h>

#include "tmux.h"

/*
 * Move a session before or after another session.
 */

static enum cmd_retval	cmd_move_session_exec(struct cmd *,
			    struct cmdq_item *);

const struct cmd_entry cmd_move_session_entry = {
	.name = "move-session",
	.alias = NULL,

	.args = { "abs:t:", 0, 0, NULL },
	.usage = "[-ab] " CMD_SRCDST_SESSION_USAGE,

	.source = { 's', CMD_FIND_SESSION, 0 },
	.target = { 't', CMD_FIND_SESSION, 0 },

	.flags = CMD_AFTERHOOK,
	.exec = cmd_move_session_exec
};

static enum cmd_retval
cmd_move_session_exec(struct cmd *self, struct cmdq_item *item)
{
	struct args		*args = cmd_get_args(self);
	struct client		*c;
	struct session		*src = cmdq_get_source(item)->s;
	struct session		*dst = cmdq_get_target(item)->s;
	struct session		*s;
	u_int			 old, new;
	int			 after = args_has(args, 'a');
	int			 before = args_has(args, 'b');

	if (after == before) {
		cmdq_error(item, "exactly one of -a or -b must be specified");
		return (CMD_RETURN_ERROR);
	}
	if (src == dst)
		return (CMD_RETURN_NORMAL);

	old = src->order;
	if (before)
		new = dst->order - (old < dst->order);
	else
		new = dst->order + (old > dst->order);

	RB_FOREACH(s, sessions, &sessions) {
		if (s == src)
			continue;
		if (new < old && s->order >= new && s->order < old)
			s->order++;
		else if (new > old && s->order > old && s->order <= new)
			s->order--;
	}
	src->order = new;
	TAILQ_FOREACH(c, &clients, entry)
		server_redraw_client(c);

	return (CMD_RETURN_NORMAL);
}
