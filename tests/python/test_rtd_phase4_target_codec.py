"""P4-only host parity test for the bounded STM32 canonical trace writer."""

from __future__ import annotations

import struct
import subprocess
import zlib
from pathlib import Path

from parser.codec import CHUNK_HEADER_STRUCT, GLOBAL_HEADER_STRUCT, SEGMENT_META_STRUCT, decode_trace


def test_bounded_target_trace_is_readable_by_public_parser(tmp_path: Path) -> None:
    root = Path(__file__).parents[2]
    source = tmp_path / "emit.cpp"
    executable = tmp_path / "emit"
    raw = tmp_path / "bounded.trace"
    source.write_text(
        """
        #include <fstream>
        #include "trace_api.h"
        #include "trace_target_contract.hpp"
        int main(int argc, char** argv) {
          rtd::p4::freertos_stm32f103::TargetCollector c(8, "capture:p4-codec");
          rtd::p4::freertos_stm32f103::Event a{};
          a.core_id=0; a.event_id=TRACE_EVENT_LOSS; a.timestamp=10; a.payload_len=8;
          const unsigned char p[8]={0,0,1,0,0,0,1,0};
          for (unsigned i=0;i<8;++i) a.payload[i]=p[i];
          c.Record(a); auto first=c.FlushCanonical();
          a.timestamp=20; c.Record(a); auto second=c.FlushCanonical();
          rtd::p4::freertos_stm32f103::WireBuffer one{}, two{};
          rtd::p4::freertos_stm32f103::CanonicalRawBuffer rebuilt{};
          c.FrameForUart(first, &one); c.FrameForUart(second, &two);
          unsigned char wire[2*rtd::p4::freertos_stm32f103::kMaxWireBytes]{};
          for (unsigned i=0;i<one.size;++i) wire[i]=one.bytes[i];
          for (unsigned i=0;i<two.size;++i) wire[one.size+i]=two.bytes[i];
          if (!c.RebuildRawFromUart(wire, one.size+two.size, &rebuilt)) return 2;
          std::ofstream out(argv[1], std::ios::binary);
          out.write(reinterpret_cast<const char*>(rebuilt.bytes.data()), rebuilt.size);
        }
        """,
        encoding="utf-8",
    )
    subprocess.run(
        [
            "c++", "-std=c++17", "-I", str(root / "collector/include"), "-I", str(root / "collector/target/freertos_stm32f103"),
            str(source), str(root / "collector/target/freertos_stm32f103/trace_target_contract.cpp"), "-o", str(executable),
        ],
        check=True,
        cwd=root,
    )
    subprocess.run([str(executable), str(raw)], check=True, cwd=root)

    decoded = decode_trace(raw, dataset_id="capture:p4-codec")
    assert decoded.ok, decoded.message
    payload = decoded.data
    assert payload is not None
    assert payload["header"].run_id == "capture:p4-codec"
    assert [event.seq for event in payload["events"]] == [1, 2]
    assert [meta.segment_seq for meta in payload["segment_metas"]] == [0, 1]

    data = raw.read_bytes()
    offset = 0
    for expected_segment, expected_seq in ((0, 1), (1, 2)):
        global_header = GLOBAL_HEADER_STRUCT.unpack_from(data, offset)
        assert global_header[0] == 0x54524345 and global_header[-1].rstrip(b"\0") == b"capture:p4-codec"
        offset += GLOBAL_HEADER_STRUCT.size
        segment = SEGMENT_META_STRUCT.unpack_from(data, offset)
        assert segment[0] == 0x53474D32 and segment[3] == expected_segment
        offset += SEGMENT_META_STRUCT.size
        chunk = CHUNK_HEADER_STRUCT.unpack_from(data, offset)
        assert chunk[0] == 0x43484B31 and chunk[7] == expected_seq and chunk[8] == expected_seq
        offset += CHUNK_HEADER_STRUCT.size
        chunk_payload = data[offset : offset + chunk[4]]
        assert (zlib.crc32(chunk_payload) & 0xFFFFFFFF) == chunk[10]
        offset += chunk[4]
    assert offset == len(data)
