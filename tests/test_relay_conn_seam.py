"""The seam with the vendored `selprotopy`: one channel per connection.

`selprotopy/` is vendored and hook-protected, so every private name the
toolkit touches (`_write`, `conn`, `fm_command_1`, `fast_meter_definition`,
`dnaDef`) is confined to `FastMessageChannel` in `pacct/core/relay_conn.py`.

What is worth pinning is not the wrapping -- it is the SHARING. The poll loop
and the `AsciiTargetReader` talk to the same relay over the same telnet, and
they agree through `is_clean` on which of them has to drain the buffer before
the next command. That used to be `client._buffer_clean`, an attribute this
project invented and hung on selprotopy's object; both sides read and wrote
it, so sharing was automatic and accidental.

Now it is channel state, and sharing is `channel_for()` memoising per client.
Hand the two sides different channel objects and nothing raises: they simply
disagree about the buffer, one of them skips a drain it needed, and a Fast
Meter frame comes back with the tail of an ASCII response glued to its front.
That is a wrong reading on a protection relay, discovered on a bench if you
are lucky. Hence these tests.

The loops themselves talk telnet to real hardware and are not testable here
(they were verified against a SEL-411L, 451, 487E, 751 and 311C on the bench).
What IS testable is the wiring, which is where a refactor would break it.
"""

from __future__ import annotations

import gc

import pytest

from pacct.core.relay_conn import FastMessageChannel, channel_for
from pacct.core.target_region import AsciiTargetReader


class _FakeClient:
    """Stands in for a `SELClient` purely as an identity.

    Nothing here touches the network: every assertion below is about which
    object you get back, not about what travels over it.
    """


class TestOneChannelPerConnection:

    def test_the_same_client_gives_back_the_same_channel(self):
        client = _FakeClient()
        assert channel_for(client) is channel_for(client)

    def test_different_clients_get_different_channels(self):
        """Two relays, two telnets, two buffer states. Sharing a channel
        across connections would make one relay's drain decision depend on
        the other's traffic."""
        assert channel_for(_FakeClient()) is not channel_for(_FakeClient())

    def test_the_target_reader_shares_the_poll_loop_channel(self):
        """THE regression this file exists for.

        `poll_loop_tar` reads analogs through the channel and digitals through
        the `AsciiTargetReader`, alternating on one telnet. If these were two
        channels, `mark_clean()` on one side would be invisible to the other.
        """
        client = _FakeClient()
        assert AsciiTargetReader(client).channel is channel_for(client)

    def test_the_clean_flag_is_visible_from_both_sides(self):
        client = _FakeClient()
        poll_side = channel_for(client)
        reader_side = AsciiTargetReader(client).channel

        poll_side.mark_clean()
        assert reader_side.is_clean, "o reader nao viu o mark_clean do poll"

        reader_side.mark_dirty()
        assert not poll_side.is_clean, "o poll nao viu o mark_dirty do reader"

    def test_a_channel_starts_dirty(self):
        """Right after login/autoconfig there is still ASCII echo in flight,
        so the first poll iteration must drain."""
        assert not channel_for(_FakeClient()).is_clean


class TestNothingIsHungOnSelprotopysObject:

    def test_the_client_gets_no_attribute_of_ours(self):
        """`_buffer_clean` was ours, not selprotopy's, and it lived on their
        object. Nothing should write to the client any more."""
        client = _FakeClient()
        before = set(vars(client))
        ch = channel_for(client)
        ch.mark_clean()
        ch.mark_dirty()
        AsciiTargetReader(client).channel.mark_clean()
        assert set(vars(client)) == before
        assert not hasattr(client, "_buffer_clean")

    def test_the_registry_does_not_keep_clients_alive(self):
        """It is a WeakKeyDictionary on purpose: the GLV opens and closes
        connections for the life of the process, and a strong map would pin
        every relay client -- and its socket -- forever."""
        import pacct.core.relay_conn as rc

        client = _FakeClient()
        channel_for(client)
        assert len(rc._CHANNELS) >= 1
        del client
        gc.collect()
        assert all(k is not None for k in rc._CHANNELS.keys())


class TestTheReaderDoesNotNeedARelayToLoadACache:

    def test_a_reader_can_be_built_without_a_client(self):
        """Half of `AsciiTargetReader` (cache load/save, layout assembly) never
        talks to a relay, and the tests for that half pass `client=None`. The
        channel is therefore lazy -- building it eagerly would make
        `channel_for(None)` raise, since `None` cannot be weak-referenced."""
        reader = AsciiTargetReader(client=None)
        assert reader.layout.bit_to_pos == {}

    def test_asking_that_reader_for_a_channel_is_what_raises(self):
        """Not a wart worth hiding: with no client there is nothing to talk
        to, and failing at the point of I/O is the honest place to fail."""
        with pytest.raises(TypeError):
            AsciiTargetReader(client=None).channel  # noqa: B018


class TestTheChannelExposesWhatTheLoopsNeed:

    def test_the_vocabulary_the_poll_loops_call(self):
        """If a selprotopy resync renames something, these are the names that
        have to keep working -- and this file is the only place that adapts."""
        for name in ("fm_marker", "fast_meter_definition", "dna_definition",
                     "is_clean"):
            assert isinstance(getattr(FastMessageChannel, name), property)
        for name in ("send_fast_meter", "send_target_region", "send_ascii",
                     "read_available", "wait_readable", "drain",
                     "mark_clean", "mark_dirty"):
            assert callable(getattr(FastMessageChannel, name))
