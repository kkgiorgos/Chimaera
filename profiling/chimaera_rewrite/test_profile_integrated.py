import unittest

from profile_integrated import dma_intervals, image_fifo_join


class TraceIntervals(unittest.TestCase):
    def test_interleaved_directions_and_reused_slot_cookies(self):
        records = dma_intervals([
            '1000: board.dma: RX HEADER cookie=1 bytes=8388608',
            '2000: board.dma: TX HEADER cookie=1 bytes=64',
            '3000: board.dma: RX PAYLOAD_BEGIN cookie=1 bytes=1843400',
            '4000: board.dma: TX PAYLOAD_BEGIN cookie=1 bytes=64',
            '5000: board.dma: TX COMPLETE cookie=1 bytes=64',
            '5000: board.dma: IRQ causes=0x2 asserted=0 command=0x6',
            '6000: board.dma: IRQ causes=0 asserted=1 command=0x6',
            '9000: board.dma: RX COMPLETE cookie=1 bytes=1843400',
            '9000: board.dma: IRQ causes=0x1 asserted=0 command=0x6',
            '10000: board.dma: IRQ causes=0 asserted=1 command=0x6',
            '10000: board.dma: RX HEADER cookie=1 bytes=8388608',
            '11000: board.dma: RX PAYLOAD_BEGIN cookie=1 bytes=80',
            '12000: board.dma: RX COMPLETE cookie=1 bytes=80',
        ])
        self.assertEqual([r['bytes'] for r in records], [64, 1843400, 80])
        self.assertEqual([r['payload_ns'] for r in records], [1, 6, 1])
        self.assertEqual(records[1]['header_to_payload_ns'], 2)
        self.assertEqual(records[0]['completion_to_ack_ns'], 1)
        self.assertEqual(records[1]['completion_to_ack_ns'], 1)

    def test_fifo_attribution_requires_every_capture_and_matching_bulk_count(self):
        transfers = [dict(direction='RX', bytes=1843400) for _ in range(2)]
        events = [dict(side=side, event=event, stream=stream, scheduled_ns=str(capture),
                       publish_ns=str(capture + 10))
                  for side, event in [('host', 'send'), ('guest', 'receive')]
                  for stream in ('left_image', 'right_image') for capture in (0, 100)]
        joined, error = image_fifo_join(transfers, events)
        self.assertIsNone(error)
        self.assertEqual(sorted(joined), [0, 100])
        self.assertEqual(joined[100]['publish_ns'], 110)
        self.assertIn('receipt', image_fifo_join(transfers, events[:-1])[1])
        self.assertIn('count/size', image_fifo_join(transfers[:-1], events)[1])
        self.assertIn('receipt', image_fifo_join(transfers, events + [events[-1]])[1])


if __name__ == '__main__':
    unittest.main()
