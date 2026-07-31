import hashlib, importlib.util, json, os, pty, shutil, struct, subprocess, sys, tempfile, threading, time, unittest
from unittest import mock
from pathlib import Path

ROOT=Path(__file__).parents[1]; SCRIPTS=ROOT/'scripts'; PINMAP=ROOT/'pinmaps/stm32f103zet6_fire_v2_phase1.json'; CANDIDATE_PINMAP=ROOT/'pinmaps/stm32f103zet6_alientek_elite_v2_phase1.json'; CANDIDATE_BOARD_ID='alientek-atk-dnf103-v2-f103zet6-05d7ff34-334e5630-43057222'
PREFLIGHT_TEMPLATE=ROOT/'candidate_preflight'/'alientek_elite_f103ze_preflight_template.json'
class ToolTests(unittest.TestCase):
 def digest(self,path): return hashlib.sha256(path.read_bytes()).hexdigest()
 def minimum_metadata(self,d,variant='GPIO_UART',pinmap=PINMAP,board_id='fire-f103zet6-v2-1',target=None):
  requirements={'BASE':(False,'calibration_20mhz',[],20_000_000),'GPIO_ONLY':(False,'calibration_20mhz',['0','1'],20_000_000),'GPIO_UART':(True,'calibration_20mhz',['0','1'],20_000_000),'GPIO_UART_RECORDER':(True,'calibration_20mhz',['0','1','7'],20_000_000),'TASK_SMOKE':(True,'calibration_20mhz',['0','1','2','3'],20_000_000),'MUTEX_SMOKE':(True,'calibration_20mhz',['0','1','4','5'],20_000_000),'IRQ_SMOKE':(True,'irq_precision_100mhz',['0','1','6'],100_000_000),'COMBINED_SMOKE':(True,'irq_precision_100mhz',[str(i) for i in range(8)],100_000_000)}[variant]
  for name,value in {'firmware.bin':'firmware','firmware.elf':'elf','build.json':'{}','observer.raw':'raw observer','observer.csv':'timestamp_s,ch0\n0,0\n'}.items(): (d/name).write_text(value)
  if requirements[0]:
   (d/'uart.txt').write_text('RTD1 EPOCH_BEGIN seq=1\nRTD1 EPOCH_END seq=1\n')
   (d/'clock.json').write_text(json.dumps({'drift_ppm':0.0,'alignment_residual_max_s':0.0,'absolute_error_max_s':0.0}))
  (d/'observer_summary.json').write_text(json.dumps({'epoch_begin_sequences':[1] if requirements[0] else [],'epoch_end_sequences':[1] if requirements[0] else [],'nonflat_channels':requirements[2],'capture_complete':True,'observer_file_sha256':self.digest(d/'observer.raw'),'normalized_observer_file_sha256':self.digest(d/'observer.csv')}))
  pm=json.loads(pinmap.read_text()); fields={'firmware_file':'firmware.bin','elf_file':'firmware.elf','build_config_file':'build.json','observer_file':'observer.raw','normalized_observer_file':'observer.csv','observer_summary_file':'observer_summary.json'}
  if requirements[0]: fields.update({'uart_file':'uart.txt','clock_analysis_file':'clock.json'})
  expected_target={'target':'stm32f103ze','probe_uid':'0001A0000001'} if target is None else target
  m={'schema_version':'phase1-capture-v2','pin_map_version':pm['schema_version'],'capture_id':'c','session_id':'s','board_id':board_id,'firmware_variant':variant,'pinmap_sha256':self.digest(pinmap),'observer':{'sample_rate_hz':requirements[3],'capture_profile':requirements[1],'channels':{str(k):{'role':v['role'],'gpio':v['gpio']} for k,v in pm['channels'].items()}},'uart':{key:pm['uart'][key] for key in ('device','peripheral','tx_gpio','rx_gpio','format')},'target':expected_target,'declared_changes':[],**fields}
  for field,name in fields.items(): m[field.replace('_file','_sha256')]=self.digest(d/name)
  m['build_config_sha256']=self.digest(d/'build.json'); m['observer_summary_sha256']=self.digest(d/'observer_summary.json')
  if requirements[0]: m['clock_analysis_sha256']=self.digest(d/'clock.json')
  return m
 def validate(self,d,m,*extra,pinmap=PINMAP):
  mp=d/'meta.json';mp.write_text(json.dumps(m));return subprocess.run([sys.executable,SCRIPTS/'validate_capture.py',mp,'--pinmap',pinmap,'--min-sample-rate-hz','1000000',*extra],capture_output=True,text=True)
 def test_clock(self):
  with tempfile.TemporaryDirectory() as raw:
   d=Path(raw);(d/'edges.csv').write_text('timestamp_s\n0\n0.001\n0.002\n');(d/'uart.txt').write_text('RTD1 EPOCH_BEGIN seq=1 tick=0\nRTD1 EPOCH_BEGIN seq=2 tick=1\n');(d/'epochs.json').write_text(json.dumps({'epochs':[{'sequence':1,'timestamp_s':0},{'sequence':2,'timestamp_s':.001}]}));out=d/'clock.json';r=subprocess.run([sys.executable,SCRIPTS/'analyze_clock.py',d/'edges.csv','--period-s','0.001','--uart-log',d/'uart.txt','--observer-epochs',d/'epochs.json','--output',out],capture_output=True,text=True);self.assertEqual(r.returncode,0,r.stderr);self.assertEqual(json.loads(out.read_text())['drift_ppm'],0)
 def test_clock_without_uart_alignment(self):
  with tempfile.TemporaryDirectory() as raw:
   d=Path(raw);(d/'edges.csv').write_text('timestamp_s,CH1\n0,0\n.0005,1\n.001,0\n.0015,1\n.002,0\n.0025,1\n');out=d/'clock.json';r=subprocess.run([sys.executable,SCRIPTS/'analyze_clock.py',d/'edges.csv','--period-s','0.001','--calibration-column','CH1','--output',out],capture_output=True,text=True);self.assertEqual(r.returncode,0,r.stderr);data=json.loads(out.read_text());self.assertEqual(data['alignment_residual_max_s'],None);self.assertNotIn('uart_log_sha256',data['input_hashes'])
 def test_prepare_dsview_observer(self):
  with tempfile.TemporaryDirectory() as raw:
   d=Path(raw);raw_file=d/'capture.dsl';raw_file.write_text('raw');csv_file=d/'capture.csv';csv_file.write_text('; DSView export\nTime(s), 0, 1, 2, 3, 4, 5, 6, 7\n0,0,0,0,0,0,0,0,0\n0.001,1,1,0,0,0,0,0,0\n0.002,0,0,0,0,0,0,0,0\n0.003,1,1,0,0,0,0,0,0\n');normalized=d/'normalized.csv';summary=d/'summary.json';epochs=d/'epochs.json';r=subprocess.run([sys.executable,SCRIPTS/'prepare_dsview_observer.py',csv_file,'--raw-observer',raw_file,'--epoch-sequence-start','7','--expected-duration-s','.003','--normalized-output',normalized,'--summary-output',summary,'--epochs-output',epochs],capture_output=True,text=True);self.assertEqual(r.returncode,0,r.stderr);data=json.loads(summary.read_text());self.assertEqual(data['nonflat_channels'],['0','1']);self.assertEqual(data['duration_s'],.003);self.assertEqual([x['sequence'] for x in json.loads(epochs.read_text())['epochs']],[7,8])
 def test_prepare_dsview_observer_records_explicit_leading_epoch_discard(self):
  with tempfile.TemporaryDirectory() as raw:
   d=Path(raw);raw_file=d/'capture.dsl';raw_file.write_text('raw');csv_file=d/'capture.csv';csv_file.write_text('Time(s), 0, 1, 2, 3, 4, 5, 6, 7\n0,0,0,0,0,0,0,0,0\n0.001,1,1,0,0,0,0,0,0\n0.002,0,0,0,0,0,0,0,0\n0.003,1,1,0,0,0,0,0,0\n');normalized=d/'normalized.csv';summary=d/'summary.json';epochs=d/'epochs.json';r=subprocess.run([sys.executable,SCRIPTS/'prepare_dsview_observer.py',csv_file,'--raw-observer',raw_file,'--epoch-sequence-start','8','--discard-leading-epochs','1','--expected-duration-s','.003','--normalized-output',normalized,'--summary-output',summary,'--epochs-output',epochs],capture_output=True,text=True);self.assertEqual(r.returncode,0,r.stderr);data=json.loads(summary.read_text());self.assertEqual(data['discarded_leading_epoch_count'],1);self.assertEqual(data['discarded_leading_epoch_timestamps_s'],[.001]);self.assertEqual([x['sequence'] for x in json.loads(epochs.read_text())['epochs']],[8])
 def test_prepare_dsview_observer_accepts_irq_three_channel_csv(self):
  with tempfile.TemporaryDirectory() as raw:
   d=Path(raw);raw_file=d/'capture.dsl';raw_file.write_text('raw');csv_file=d/'capture.csv';csv_file.write_text('Time(s), 0, 1, 6\n0,0,0,0\n0.001,1,1,1\n0.002,0,0,0\n0.003,1,1,1\n');normalized=d/'normalized.csv';summary=d/'summary.json';epochs=d/'epochs.json';r=subprocess.run([sys.executable,SCRIPTS/'prepare_dsview_observer.py',csv_file,'--raw-observer',raw_file,'--epoch-sequence-start','1','--expected-duration-s','.003','--channels','0,1,6','--normalized-output',normalized,'--summary-output',summary,'--epochs-output',epochs],capture_output=True,text=True);self.assertEqual(r.returncode,0,r.stderr);data=json.loads(summary.read_text());self.assertEqual(data['captured_channels'],['0','1','6']);self.assertEqual(data['nonflat_channels'],['0','1','6'])
 def test_prepare_dsview_observer_allows_static_baseline(self):
  with tempfile.TemporaryDirectory() as raw:
   d=Path(raw);raw_file=d/'capture.dsl';raw_file.write_text('raw');csv_file=d/'capture.csv';csv_file.write_text('Time(s), 0, 1, 2, 3, 4, 5, 6, 7\n0,0,0,0,0,0,0,0,0\n0.003,0,0,0,0,0,0,0,0\n');normalized=d/'normalized.csv';summary=d/'summary.json';epochs=d/'epochs.json';r=subprocess.run([sys.executable,SCRIPTS/'prepare_dsview_observer.py',csv_file,'--raw-observer',raw_file,'--epoch-sequence-start','1','--expected-duration-s','.003','--allow-no-epochs','--normalized-output',normalized,'--summary-output',summary,'--epochs-output',epochs],capture_output=True,text=True);self.assertEqual(r.returncode,0,r.stderr);data=json.loads(summary.read_text());self.assertEqual(data['nonflat_channels'],[]);self.assertEqual(data['epoch_begin_sequences'],[])
 def test_prepare_dsview_observer_uses_dsview_static_sample_metadata(self):
  with tempfile.TemporaryDirectory() as raw:
   d=Path(raw);raw_file=d/'capture.dsl';raw_file.write_text('raw');csv_file=d/'capture.csv';csv_file.write_text('; Sample rate: 20 MHz\n; Sample count: 100.768 M Samples\nTime(s), 0, 1, 2, 3, 4, 5, 6, 7\n0,0,0,0,0,0,0,0,0\n');normalized=d/'normalized.csv';summary=d/'summary.json';epochs=d/'epochs.json';r=subprocess.run([sys.executable,SCRIPTS/'prepare_dsview_observer.py',csv_file,'--raw-observer',raw_file,'--epoch-sequence-start','1','--expected-duration-s','5','--allow-no-epochs','--normalized-output',normalized,'--summary-output',summary,'--epochs-output',epochs],capture_output=True,text=True);self.assertEqual(r.returncode,0,r.stderr);data=json.loads(summary.read_text());self.assertEqual(data['duration_s'],5.0384);self.assertEqual(data['duration_source'],'dsview-sample-count-and-rate');self.assertTrue(data['capture_complete'])
 def test_validator_accepts_complete_evidence(self):
  with tempfile.TemporaryDirectory() as raw:
   d=Path(raw);self.assertEqual(self.validate(d,self.minimum_metadata(d)).returncode,0)
 def test_validator_accepts_non_uart_baselines(self):
  with tempfile.TemporaryDirectory() as raw:
   d=Path(raw)
   for variant in ('BASE','GPIO_ONLY'):
    child=d/variant;child.mkdir();self.assertEqual(self.validate(child,self.minimum_metadata(child,variant)).returncode,0)
 def test_validator_accepts_frozen_alientek_contract_and_binds_mcu_uid(self):
  with tempfile.TemporaryDirectory() as raw:
   d=Path(raw);target={'target':'stm32f103ze','probe_uid':'0001A0000001','mcu_uid_words':['0x05d7ff34','0x334e5630','0x43057222']};m=self.minimum_metadata(d,pinmap=CANDIDATE_PINMAP,board_id=CANDIDATE_BOARD_ID,target=target)
   self.assertEqual(self.validate(d,m,pinmap=CANDIDATE_PINMAP).returncode,0)
   m['target']['mcu_uid_words'][0]='wrong';self.assertIn('target_identity_mismatch',self.validate(d,m,pinmap=CANDIDATE_PINMAP).stdout)
 def test_combined_contract_requires_full_20mhz_capture_for_each_board(self):
  for pinmap,board_id,target in ((PINMAP,'fire-f103zet6-v2-1',None),(CANDIDATE_PINMAP,CANDIDATE_BOARD_ID,{'target':'stm32f103ze','probe_uid':'0001A0000001','mcu_uid_words':['0x05d7ff34','0x334e5630','0x43057222']})):
   with tempfile.TemporaryDirectory() as raw:
    d=Path(raw);m=self.minimum_metadata(d,'COMBINED_SMOKE',pinmap,board_id,target);m['observer'].update({'sample_rate_hz':20_000_000,'capture_profile':'calibration_20mhz','expected_duration_s':5.0});summary=json.loads((d/'observer_summary.json').read_text());summary.update({'captured_channels':[str(i) for i in range(8)],'duration_s':5.0});(d/'observer_summary.json').write_text(json.dumps(summary));m['observer_summary_sha256']=self.digest(d/'observer_summary.json');self.assertEqual(self.validate(d,m,pinmap=pinmap).returncode,0)
    summary['captured_channels']=['0','1','6'];(d/'observer_summary.json').write_text(json.dumps(summary));m['observer_summary_sha256']=self.digest(d/'observer_summary.json');self.assertIn('observer_capture_channel_set_mismatch',self.validate(d,m,pinmap=pinmap).stdout)
 def test_validator_rejects_empty_observer(self):
  with tempfile.TemporaryDirectory() as raw:
   d=Path(raw);m=self.minimum_metadata(d);(d/'observer.raw').write_text('');self.assertIn('empty_or_missing_observer_file',self.validate(d,m).stdout)
 def test_validator_rejects_missing_epoch_pair(self):
  with tempfile.TemporaryDirectory() as raw:
   d=Path(raw);m=self.minimum_metadata(d);(d/'uart.txt').write_text('RTD1 EPOCH_BEGIN seq=1\n');m['uart_sha256']=self.digest(d/'uart.txt');self.assertIn('uart_missing_epoch_end',self.validate(d,m).stdout)
 def test_validator_rejects_declared_duration_mismatch(self):
  with tempfile.TemporaryDirectory() as raw:
   d=Path(raw);m=self.minimum_metadata(d);m['observer']['expected_duration_s']=5;summary=json.loads((d/'observer_summary.json').read_text());summary['duration_s']=20;(d/'observer_summary.json').write_text(json.dumps(summary));m['observer_summary_sha256']=self.digest(d/'observer_summary.json');self.assertIn('observer_duration_mismatch',self.validate(d,m).stdout)
 def test_validator_rejects_artifact_hash_mismatch(self):
  with tempfile.TemporaryDirectory() as raw:
   d=Path(raw);m=self.minimum_metadata(d);m['elf_sha256']='0'*64;self.assertIn('elf_file_hash_mismatch',self.validate(d,m).stdout)
 def test_validator_rejects_channel_mapping_mismatch(self):
  with tempfile.TemporaryDirectory() as raw:
   d=Path(raw);m=self.minimum_metadata(d);m['observer']['channels']['0']['gpio']='PC0';self.assertIn('channel_mapping_mismatch',self.validate(d,m).stdout)
 def test_validator_rejects_retired_pinmap_and_uart(self):
  with tempfile.TemporaryDirectory() as raw:
   d=Path(raw);m=self.minimum_metadata(d);m['pin_map_version']='phase1-pinmap-v1';m['uart']['peripheral']='USART3';o=self.validate(d,m).stdout;self.assertIn('retired_or_mismatched_pinmap_version',o);self.assertIn('uart_mapping_mismatch',o)
 def test_validator_rejects_board_or_probe_binding_mismatch(self):
  with tempfile.TemporaryDirectory() as raw:
   d=Path(raw);m=self.minimum_metadata(d);m['board_id']='other-board';m['target']['probe_uid']='wrong';o=self.validate(d,m).stdout;self.assertIn('board_id_mismatch',o);self.assertIn('target_binding_mismatch',o)
 def test_pinmap_v2_gpio_roles_and_led_polarity(self):
  pinmap=json.loads(PINMAP.read_text());self.assertEqual(pinmap['schema_version'],'phase1-pinmap-v2');self.assertEqual([pinmap['channels'][str(i)]['gpio'] for i in range(8)],['PC2','PC3','PC4','PC5','PC6','PC7','PB5','PB0']);self.assertEqual(pinmap['uart']['peripheral'],'USART1');self.assertEqual(pinmap['uart']['tx_gpio'],'PA9');self.assertEqual(pinmap['channels']['6']['led_correlation'],'logical_active=LED_off');self.assertEqual(pinmap['channels']['7']['led_correlation'],'logical_active=LED_off')
  candidate=json.loads(CANDIDATE_PINMAP.read_text());self.assertEqual(candidate['board_id'],CANDIDATE_BOARD_ID);self.assertEqual(candidate['channels']['6']['board_load'],'on-board DS0 red LED, low-active');self.assertEqual(candidate['channels']['7']['board_load'],'TFTLCD backlight control (LCD_BL); connected LCD module state is setup-specific');self.assertNotIn('led_correlation',candidate['channels']['7'])
 def test_uart_and_flash_restore_dry_runs(self):
  uart=subprocess.run([sys.executable,SCRIPTS/'capture_uart.py','--help'],capture_output=True,text=True);self.assertIn('/dev/ttyUSB0',uart.stdout);flash_source=(SCRIPTS/'flash_firmware.sh').read_text();self.assertIn("'reset hardware'",flash_source);self.assertNotIn('pyocd reset --target',flash_source)
  restore=subprocess.run([SCRIPTS/'restore_keil_backup.sh','--dry-run'],capture_output=True,text=True);self.assertEqual(restore.returncode,0,restore.stderr);self.assertIn('0001A0000001',restore.stdout)
  with tempfile.TemporaryDirectory() as raw:
   d=Path(raw);elf=d/'image.elf';elf.write_bytes(b'ELF fixture');m=d/'flash_meta.json';m.write_text(json.dumps({'pin_map_version':'phase1-pinmap-v2','firmware_variant':'GPIO_UART','board_id':'fire-f103zet6-v2-1','session_id':'preflash','firmware_sha256':'f'*64,'build_config_sha256':'b'*64,'pinmap_sha256':self.digest(PINMAP),'elf_sha256':self.digest(elf),'target':{'target':'stm32f103ze','probe_uid':'0001A0000001'}}));flash=subprocess.run([SCRIPTS/'flash_firmware.sh','--metadata',m,'--dry-run',elf],capture_output=True,text=True);self.assertEqual(flash.returncode,0,flash.stderr);self.assertIn('0001A0000001',flash.stdout)
   m.write_text(json.dumps({'pin_map_version':'phase1-pinmap-v2','firmware_variant':'GPIO_UART','elf_sha256':self.digest(elf)}));flash=subprocess.run([SCRIPTS/'flash_firmware.sh','--metadata',m,'--dry-run',elf],capture_output=True,text=True);self.assertNotEqual(flash.returncode,0)
   m=json.loads((d/'flash_meta.json').read_text());m['board_id']='other-board';(d/'flash_meta.json').write_text(json.dumps(m));flash=subprocess.run([SCRIPTS/'flash_firmware.sh','--metadata',d/'flash_meta.json','--dry-run',elf],capture_output=True,text=True);self.assertNotEqual(flash.returncode,0)
   candidate={'pin_map_version':'phase1-pinmap-v2','firmware_variant':'GPIO_UART','board_id':CANDIDATE_BOARD_ID,'session_id':'preflash','firmware_sha256':'f'*64,'build_config_sha256':'b'*64,'pinmap_sha256':self.digest(CANDIDATE_PINMAP),'elf_sha256':self.digest(elf),'target':{'target':'stm32f103ze','probe_uid':'0001A0000001','mcu_uid_words':['0x05d7ff34','0x334e5630','0x43057222']}}
   (d/'flash_meta.json').write_text(json.dumps(candidate));flash=subprocess.run([SCRIPTS/'flash_firmware.sh','--metadata',d/'flash_meta.json','--dry-run',elf],capture_output=True,text=True);self.assertEqual(flash.returncode,0,flash.stderr);self.assertIn(CANDIDATE_BOARD_ID,flash.stdout)
 def test_uart_capture_uses_termios_and_never_imports_pyserial(self):
  source=(SCRIPTS/'capture_uart.py').read_text();self.assertIn('capture_with_termios(a.port, a.baud, a.seconds, out, a.ready_file, a.modem_lines, a.access_mode, a.release_on_sigusr1, a.restore_rts_on_release)',source);self.assertIn('termios.tcflush(fd, termios.TCIFLUSH)',source);self.assertIn('configure_modem_lines(fd, modem_lines)',source);self.assertIn('release_on_sigusr1',source);self.assertIn('restore_rts_on_release',source);self.assertIn('attrs[2] &= ~termios.HUPCL',source);self.assertIn('access_mode == "read-only"',source);self.assertIn('access_mode == "read-write"',source);self.assertNotIn('import serial',source)
 def test_uart_modem_line_modes_are_explicit_and_auditable(self):
  spec=importlib.util.spec_from_file_location('capture_uart',SCRIPTS/'capture_uart.py');module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
  state=[module.MODEM_OUTPUT_BITS]
  def ioctl(_fd, request, value):
   bits=struct.unpack('I',value)[0]
   if request==module.termios.TIOCMGET: return struct.pack('I',state[0])
   if request==module.termios.TIOCMBIC: state[0]&=~bits;return b''
   if request==module.termios.TIOCMBIS: state[0]|=bits;return b''
   raise AssertionError(request)
  with mock.patch.object(module.fcntl,'ioctl',side_effect=ioctl):
   self.assertEqual(module.configure_modem_lines(1,'preserve'),(module.MODEM_OUTPUT_BITS,module.MODEM_OUTPUT_BITS))
   self.assertEqual(module.configure_modem_lines(1,'clear'),(module.MODEM_OUTPUT_BITS,0))
   self.assertEqual(module.configure_modem_lines(1,'set'),(0,module.MODEM_OUTPUT_BITS))
   self.assertEqual(module.configure_modem_lines(1,'clear-dtr'),(module.MODEM_OUTPUT_BITS,module.termios.TIOCM_RTS))
   self.assertEqual(module.configure_modem_lines(1,'clear-rts'),(module.termios.TIOCM_RTS,0))
   state[0]=module.MODEM_OUTPUT_BITS
   self.assertEqual(module.configure_modem_lines(1,'explicit-mask-0x2'),(module.MODEM_OUTPUT_BITS,module.termios.TIOCM_DTR))
   state[0]=module.MODEM_OUTPUT_BITS
   with mock.patch.object(module.time,'sleep') as pause:
    self.assertEqual(module.configure_modem_lines(1,'flash-reset'),(module.MODEM_OUTPUT_BITS,module.termios.TIOCM_DTR))
   pause.assert_called_once_with(.05)
   state[0]=module.MODEM_OUTPUT_BITS
   with mock.patch.object(module.time,'sleep') as pause:
    self.assertEqual(module.configure_modem_lines(1,'flash-reset-rts-enable'),(module.MODEM_OUTPUT_BITS,module.MODEM_OUTPUT_BITS))
   pause.assert_called_once_with(.05)
   state[0]=module.MODEM_OUTPUT_BITS
   self.assertEqual(module.configure_modem_lines(1,'hold-flash-reset'),(module.MODEM_OUTPUT_BITS,0))
   self.assertEqual(module.release_held_flash_reset(1),module.termios.TIOCM_DTR)
   self.assertEqual(module.release_held_flash_reset(1,restore_rts=True),module.MODEM_OUTPUT_BITS)
 def test_uart_ready_file_follows_termios_setup_and_reader_stays_open(self):
  spec=importlib.util.spec_from_file_location('capture_uart',SCRIPTS/'capture_uart.py');module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
  master,slave=pty.openpty()
  try:
   with tempfile.TemporaryDirectory() as raw:
    d=Path(raw);out=d/'uart.log';ready=d/'uart.ready';failure=[]
    def modem_ioctl(_fd, request, value):
     if request==module.termios.TIOCMGET: return struct.pack('I',module.MODEM_OUTPUT_BITS)
     raise AssertionError(request)
    def reader():
     try:
      with out.open('xb') as handle: module.capture_with_termios(os.ttyname(slave),115200,.2,handle,ready)
     except Exception as exc: failure.append(exc)
    with mock.patch.object(module.fcntl,'ioctl',side_effect=modem_ioctl):
     worker=threading.Thread(target=reader);worker.start()
     deadline=time.monotonic()+1
     while not ready.exists() and time.monotonic()<deadline: time.sleep(.005)
     self.assertTrue(ready.exists());self.assertIn('uart_open_configured=1\n',ready.read_text());self.assertIn('uart_modem_lines_before=',ready.read_text());self.assertIn('uart_modem_lines_after=',ready.read_text())
     os.write(master,b'RTD1 BOOT test\r\n');worker.join(1)
     self.assertFalse(worker.is_alive());self.assertEqual(failure,[]);self.assertIn(b'RTD1 BOOT test',out.read_bytes())
  finally:
   os.close(master);os.close(slave)
 def test_candidate_uart_control_mask_is_explicit_and_limited(self):
  spec=importlib.util.spec_from_file_location('candidate_uart_control',SCRIPTS/'candidate_uart_control_preflight.py');module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
  state=[module.MODEM_OUTPUT_BITS]
  def ioctl(_fd, request, value):
   bits=struct.unpack('I',value)[0]
   if request==module.termios.TIOCMGET: return struct.pack('I',state[0])
   if request==module.termios.TIOCMBIC: state[0]&=~bits;return b''
   if request==module.termios.TIOCMBIS: state[0]|=bits;return b''
   raise AssertionError(request)
  with mock.patch.object(module.fcntl,'ioctl',side_effect=ioctl):
   self.assertEqual(module.set_modem_mask(1,module.termios.TIOCM_DTR),module.termios.TIOCM_DTR)
   with self.assertRaises(ValueError): module.set_modem_mask(1,8)
 def test_rtos_capture_gate_is_single_use_and_swd_bound(self):
  source=Path('/home/zzq/embedded/stm32f103_env/workspace/rtd_phase1_feasibility/rtos_smoke/src/main.c').read_text();cmake=Path('/home/zzq/embedded/stm32f103_env/workspace/rtd_phase1_feasibility/rtos_smoke/CMakeLists.txt').read_text();gate=(SCRIPTS/'arm_capture_gate.sh').read_text();self.assertIn('while (!capture_armed)',source);self.assertIn('RTD1 CAPTURE_ARMED source=swd_gate',source);self.assertIn('RTD_CAPTURE_GATED',cmake);self.assertIn('0001A0000001',gate);self.assertIn('capture_armed',gate);self.assertIn("'capture_gate_address':gate.get('address'",gate);self.assertIn('xTaskCreate(task_heartbeat, "heart", 256, 0, 4, 0);',source);self.assertIn('taskENTER_CRITICAL(); uart("RTD1 TRACE seq=")',source)
  self.assertIn('capture_armed = 0u;',source)
 def test_capture_gate_emits_persistent_session_command_only(self):
  gate=(SCRIPTS/'arm_capture_gate.sh').read_text();reader=(SCRIPTS/'capture_gate_uart.py').read_text();self.assertIn('--emit-persistent-command',gate);self.assertIn('persistent_session_command=write32',gate);self.assertNotIn('pyocd commander',gate);self.assertIn('--exit-on-verified',reader)
 def test_capture_gate_requires_matching_boot_ready_file(self):
  metadata=Path('/home/zzq/embedded/stm32f103_env/workspace/rtd_phase1_feasibility/evidence/h2_20260728T082308Z_baremetal_gpio_uart_alignment_fix/alientek_elite_candidate_preflight/candidate_preflash_metadata.json')
  elf=Path('/home/zzq/embedded/stm32f103_env/workspace/rtd_phase1_feasibility/rtos_smoke/build/alientek_elite_candidate_task_smoke/artifacts/rtd_phase1_freertos_TASK_SMOKE.elf')
  missing=subprocess.run([SCRIPTS/'arm_capture_gate.sh','--metadata',metadata,'--emit-persistent-command',elf],capture_output=True,text=True)
  self.assertNotEqual(missing.returncode,0);self.assertIn('--gate-ready-file',missing.stderr)
  with tempfile.TemporaryDirectory() as raw:
   d=Path(raw);ready=d/'gate.ready';ready.write_text('schema_version=phase1-gate-ready-v1\nboot_observed=1\nvariant=TASK_SMOKE\nbuild_hash=h2-alientek-elite-candidate-20260728\nsession=alientek-elite-candidate-task01\nelf_sha256='+self.digest(elf)+'\ncapture_gate_symbol=capture_armed\ncapture_gate_address=0x20000004\nmodem_policy=preserve-0x00000002\n')
   accepted=subprocess.run([SCRIPTS/'arm_capture_gate.sh','--metadata',metadata,'--gate-ready-file',ready,'--emit-persistent-command',elf],capture_output=True,text=True)
   self.assertEqual(accepted.returncode,0,accepted.stderr);self.assertIn('persistent_session_command=write32 0x20000004 0x00000001',accepted.stdout)
   ready.write_text(ready.read_text().replace('session=alientek-elite-candidate-task01','session=wrong'))
   rejected=subprocess.run([SCRIPTS/'arm_capture_gate.sh','--metadata',metadata,'--gate-ready-file',ready,'--emit-persistent-command',elf],capture_output=True,text=True)
   self.assertNotEqual(rejected.returncode,0);self.assertIn('does not match',rejected.stderr)
 def test_capture_gate_accepts_mutex_identity(self):
  sys.path.insert(0,str(SCRIPTS))
  try:
   spec=importlib.util.spec_from_file_location('capture_gate_uart',SCRIPTS/'capture_gate_uart.py');module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
  finally: sys.path.pop(0)
  with tempfile.TemporaryDirectory() as raw:
   d=Path(raw);elf=d/'mutex.elf';elf.write_bytes(b'mutex fixture')
   for variant in ('GPIO_UART_RECORDER','MUTEX_SMOKE','IRQ_SMOKE','COMBINED_SMOKE','H3_COLLECTOR_SMOKE'):
    address='0x20000000' if variant == 'H3_COLLECTOR_SMOKE' else '0x20000004';metadata=d/(variant+'.json');metadata.write_text(json.dumps({'firmware':{'variant':variant,'build_hash':'build','session_id':'session','elf_sha256':self.digest(elf),'capture_gate':{'symbol':'capture_armed','address':address}},'target':{'target':'stm32f103ze','probe_uid':'0001A0000001'}}));self.assertEqual(module.load_identity(metadata,elf)['variant'],variant)
 def test_candidate_gate_uart_requires_ordered_complete_handshake(self):
  sys.path.insert(0,str(SCRIPTS))
  try:
   spec=importlib.util.spec_from_file_location('capture_gate_uart',SCRIPTS/'capture_gate_uart.py');module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
  finally: sys.path.pop(0)
  identity={'variant':'TASK_SMOKE','build_hash':'build','session':'session'}
  boot='RTD1 BOOT firmware=phase1-freertos variant=TASK_SMOKE build_hash=build session=session pinmap=phase1-pinmap-v2 uart=USART1_PA9_115200_8N1\r\n'
  complete=boot+'RTD1 CAPTURE_ARMED source=swd_gate\r\nRTD1 EPOCH_BEGIN seq=7 tick=1 variant=TASK_SMOKE\r\nRTD1 EPOCH_END seq=7 tick=2\r\n'
  observed=module.observe_gate_sequence(complete,identity);self.assertTrue(observed['gate_sequence_verified']);self.assertEqual(observed['epoch_sequence'],7)
  self.assertFalse(module.observe_gate_sequence(boot,identity)['gate_sequence_verified'])
  early='RTD1 CAPTURE_ARMED source=swd_gate\r\n'+boot+'RTD1 EPOCH_BEGIN seq=7\r\nRTD1 EPOCH_END seq=7\r\n'
  self.assertFalse(module.observe_gate_sequence(early,identity)['gate_sequence_verified'])
  mismatch=boot+'RTD1 CAPTURE_ARMED source=swd_gate\r\nRTD1 EPOCH_BEGIN seq=7\r\nRTD1 EPOCH_END seq=8\r\n'
  self.assertFalse(module.observe_gate_sequence(mismatch,identity)['gate_sequence_verified'])
 def test_h3_collector_uart_validator_requires_attributable_overflow(self):
  with tempfile.TemporaryDirectory() as raw:
   d=Path(raw);metadata=d/'h3.json';metadata.write_text(json.dumps({'schema_version':'phase1-h3-collector-capture-v1','board_id':CANDIDATE_BOARD_ID,'firmware':{'variant':'H3_COLLECTOR_SMOKE','build_hash':'build','session_id':'session','capture_gate':{'symbol':'capture_armed','address':'0x20000000','mode':'single_use_swd'}},'target':{'target':'stm32f103ze','probe_uid':'0001A0000001','mcu_uid_words':['0x05d7ff34','0x334e5630','0x43057222']},'collector':{'capacity':64,'transport':'USART1_115200','drop_policy':'drop_new','irq_decimation':20}}));uart=d/'uart.log';uart.write_text('RTD1 BOOT firmware=phase1-freertos version=phase1-pinmap-v2 variant=H3_COLLECTOR_SMOKE build_hash=old session=old pinmap=phase1-pinmap-v2 uart=USART1_PA9_115200_8N1\nRTD1 COLLECTOR_CONFIG version=h3-smoke-v1 capacity=64 transport=USART1_115200 drop_policy=drop_new irq_decimation=20\nRTD1 BOOT firmware=phase1-freertos version=phase1-pinmap-v2 variant=H3_COLLECTOR_SMOKE build_hash=build session=session pinmap=phase1-pinmap-v2 uart=USART1_PA9_115200_8N1\nRTD1 COLLECTOR_CONFIG version=h3-smoke-v1 capacity=64 transport=USART1_115200 drop_policy=drop_new irq_decimation=20\nRTD1 CAPTURE_ARMED source=swd_gate\nRTD1 EPOCH_BEGIN seq=1 tick=2 variant=H3_COLLECTOR_SMOKE\nRTD1 EPOCH_END seq=1 tick=3\nRTD1 TRACE seq=1 tick=2 kind=1\nRTD1 TRACE seq=2 tick=2 kind=2\nRTD1 TRACE seq=34 tick=2 kind=90\nRTD1 COLLECTOR_STATUS stage=post_pressure next_seq=96 queued=0 high_watermark=64 dropped=32 overflow=32\n');out=d/'validation.json';result=subprocess.run([sys.executable,SCRIPTS/'validate_h3_collector_uart.py','--metadata',metadata,'--uart',uart,'--output',out],capture_output=True,text=True);self.assertEqual(result.returncode,0,result.stdout+result.stderr);self.assertTrue(json.loads(out.read_text())['valid'])
   uart.write_text(uart.read_text().replace('RTD1 TRACE seq=34','RTD1 TRACE seq=3'));out=d/'rejected.json';result=subprocess.run([sys.executable,SCRIPTS/'validate_h3_collector_uart.py','--metadata',metadata,'--uart',uart,'--output',out],capture_output=True,text=True);self.assertNotEqual(result.returncode,0);self.assertIn('trace_sequence_gap_missing',result.stdout)
 def test_validator_rejects_gpio_uart_disagreement(self):
  with tempfile.TemporaryDirectory() as raw:
   d=Path(raw);m=self.minimum_metadata(d);(d/'observer_summary.json').write_text(json.dumps({'epoch_begin_sequences':[2],'epoch_end_sequences':[2],'nonflat_channels':['0','1'],'capture_complete':True,'observer_file_sha256':m['observer_sha256'],'normalized_observer_file_sha256':m['normalized_observer_sha256']}));m['observer_summary_sha256']=self.digest(d/'observer_summary.json');self.assertIn('gpio_uart_epoch_disagreement',self.validate(d,m).stdout)
 def test_validator_accepts_observer_epoch_subset(self):
  with tempfile.TemporaryDirectory() as raw:
   d=Path(raw);m=self.minimum_metadata(d);(d/'uart.txt').write_text('RTD1 EPOCH_BEGIN seq=1\nRTD1 EPOCH_END seq=1\nRTD1 EPOCH_BEGIN seq=2\nRTD1 EPOCH_END seq=2\n');m['uart_sha256']=self.digest(d/'uart.txt');self.assertEqual(self.validate(d,m).returncode,0)
 def test_validator_rejects_irq_capture_below_precision_rate(self):
  with tempfile.TemporaryDirectory() as raw:
   d=Path(raw);m=self.minimum_metadata(d,'IRQ_SMOKE');m['observer']['sample_rate_hz']=20_000_000;self.assertIn('sample_rate_below_frozen_minimum',self.validate(d,m).stdout)
 def test_validator_rejects_drift_and_alignment_breach(self):
  with tempfile.TemporaryDirectory() as raw:
   d=Path(raw);m=self.minimum_metadata(d);(d/'clock.json').write_text(json.dumps({'drift_ppm':11,'alignment_residual_max_s':.2}));m['clock_analysis_sha256']=self.digest(d/'clock.json');o=self.validate(d,m,'--max-drift-ppm','10','--max-alignment-s','.1').stdout;self.assertIn('drift_exceeds_frozen_bound',o);self.assertIn('alignment_exceeds_frozen_bound',o)
 def test_validator_enforces_alientek_clock_error_bound(self):
  with tempfile.TemporaryDirectory() as raw:
   d=Path(raw);target={'target':'stm32f103ze','probe_uid':'0001A0000001','mcu_uid_words':['0x05d7ff34','0x334e5630','0x43057222']};m=self.minimum_metadata(d,pinmap=CANDIDATE_PINMAP,board_id=CANDIDATE_BOARD_ID,target=target);(d/'clock.json').write_text(json.dumps({'drift_ppm':0.0,'alignment_residual_max_s':0.0,'absolute_error_max_s':.000001}));m['clock_analysis_sha256']=self.digest(d/'clock.json');self.assertIn('calibration_error_exceeds_frozen_bound',self.validate(d,m,pinmap=CANDIDATE_PINMAP).stdout)
 def test_alientek_candidate_preflight_is_separate_and_fail_closed(self):
  with tempfile.TemporaryDirectory() as raw:
   d=Path(raw);manifest=json.loads(PREFLIGHT_TEMPLATE.read_text());p=d/'candidate_preflight_manifest.json';p.write_text(json.dumps(manifest));check=lambda: subprocess.run([sys.executable,SCRIPTS/'check_candidate_preflight.py',p],capture_output=True,text=True)
   self.assertEqual(check().returncode,0);self.assertIn('PREFLIGHT_PENDING',check().stdout)
   manifest['board_id']='must-not-freeze-here';p.write_text(json.dumps(manifest));self.assertIn('formal_capture_field_present',check().stdout)
   del manifest['board_id'];evidence=d/'pa9.dsl';evidence.write_text('raw');manifest['artifacts']=[{'id':'pa9_raw','file':'pa9.dsl','sha256':self.digest(evidence)}];manifest['findings']['pa9_tx']={'status':'pass','evidence_ids':['missing']};p.write_text(json.dumps(manifest));self.assertIn('passing_finding_missing_evidence',check().stdout)
   manifest['findings']['pa9_tx']={'status':'pending','evidence_ids':[]};manifest['findings']['physical_mapping']={'status':'pass','evidence_ids':['pa9_raw']};manifest['findings']['gpio_observer']={'status':'pass','evidence_ids':['pa9_raw']};manifest['findings']['ch340_path']={'status':'pending','evidence_ids':[]};manifest['findings']['host_serial']={'status':'pass','evidence_ids':['pa9_raw']};manifest['selected_uart_witness']='ch340_host_witness_v1';p.write_text(json.dumps(manifest));self.assertIn('PREFLIGHT_READY_FOR_SEPARATE_FREEZE_REVIEW',check().stdout)
 def test_validator_rejects_summary_source_hash_mismatch(self):
  with tempfile.TemporaryDirectory() as raw:
   d=Path(raw);m=self.minimum_metadata(d);summary=json.loads((d/'observer_summary.json').read_text());summary['observer_file_sha256']='0'*64;(d/'observer_summary.json').write_text(json.dumps(summary));m['observer_summary_sha256']=self.digest(d/'observer_summary.json');self.assertIn('observer_summary_source_hash_mismatch',self.validate(d,m).stdout)
 def test_h3_alignment_session_verifier_binds_uart_hashes(self):
  with tempfile.TemporaryDirectory() as raw:
   d=Path(raw);root=d/'repo';session=root/'sessions'/'session';session.mkdir(parents=True);external=d/'external';external.mkdir();contract=root/'hardware/rtd_pilot/contracts/contract.json';contract.parent.mkdir(parents=True);contract.write_text('{}');control=root/'hardware/rtd_pilot/scripts/verify_h3_alignment_session.py';control.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(SCRIPTS/'verify_h3_alignment_session.py',control)
   write=lambda path,text: (path.write_text(text),self.digest(path))[1]
   dsl=external/'capture.dsl';csv=external/'capture.csv';screenshot=external/'capture.png';dsl_hash=write(dsl,'dsl');csv_hash=write(csv,'csv');screenshot_hash=write(screenshot,'png')
   uart=session/'uart_raw.log';uart_hash=write(uart,'RTD1 EPOCH_BEGIN seq=1 tick=1\n');ready=session/'uart_reader.ready';ready_hash=write(ready,'ready');gate_ready=session/'gate.ready';gate_ready_hash=write(gate_ready,'gate-ready');verified=session/'gate.verified';verified_hash=write(verified,'verified')
   gate=session/'gate_receipt.json';gate_hash=write(gate,json.dumps({'uart_sha256':uart_hash}));normalized=session/'observer_normalized.csv';normalized_hash=write(normalized,'timestamp_s,CH0,CH1\n0,0,0\n');epochs=session/'observer_epochs.json';epochs_hash=write(epochs,'{}');summary=session/'observer_summary.json';summary_hash=write(summary,json.dumps({'observer_file_sha256':dsl_hash,'normalized_observer_file_sha256':normalized_hash,'source_csv_sha256':csv_hash}));analysis=session/'clock_analysis.json';analysis_hash=write(analysis,json.dumps({'input_hashes':{'uart_log_sha256':uart_hash,'calibration_csv_sha256':normalized_hash,'observer_epochs_sha256':epochs_hash}}));recompute=session/'clock_analysis_main_recompute.json';recompute_hash=write(recompute,analysis.read_text())
   contract_hash=self.digest(contract);binding=session/'v2_contract_binding.json';binding_data={'v2_decision_id':'decision','v2_effective_not_before_utc':'2026-07-31T11:00:45Z','v2_contract_path':'hardware/rtd_pilot/contracts/contract.json','v2_contract_sha256':contract_hash};manifest_binding={'decision_id':'decision','effective_not_before_utc':'2026-07-31T11:00:45Z','v2_contract_path':'hardware/rtd_pilot/contracts/contract.json','v2_contract_sha256':contract_hash};binding_hash=write(binding,json.dumps(binding_data));reset=session/'reset_action_receipt.json';reset_hash=write(reset,'{}')
   raw_artifacts={'dsview_dsl':{'external_path':str(dsl),'sha256':dsl_hash},'dsview_csv':{'external_path':str(csv),'sha256':csv_hash},'dsview_screenshot':{'external_path':str(screenshot),'sha256':screenshot_hash},'uart_raw':{'file':'uart_raw.log','sha256':uart_hash},'uart_reader_ready':{'file':'uart_reader.ready','sha256':ready_hash},'gate_ready':{'file':'gate.ready','sha256':gate_ready_hash},'gate_verified':{'file':'gate.verified','sha256':verified_hash},'gate_receipt':{'file':'gate_receipt.json','sha256':gate_hash},'v2_contract_binding':{'file':'v2_contract_binding.json','sha256':binding_hash}}
   derived_artifacts={'observer_normalized':{'file':'observer_normalized.csv','sha256':normalized_hash},'observer_summary':{'file':'observer_summary.json','sha256':summary_hash},'observer_epochs':{'file':'observer_epochs.json','sha256':epochs_hash},'clock_analysis':{'file':'clock_analysis.json','sha256':analysis_hash},'clock_analysis_main_recompute':{'file':'clock_analysis_main_recompute.json','sha256':recompute_hash}}
   manifest=session/'session_manifest.json';payload={'schema_version':'phase1-h3-alignment-session-v2','raw_artifacts':raw_artifacts,'derived_artifacts':derived_artifacts,'v2_contract_binding':manifest_binding,'independence':{'binding_sha256':binding_hash,'reset_receipt':'reset_action_receipt.json','reset_receipt_sha256':reset_hash}};manifest.write_text(json.dumps(payload))
   verify=lambda: subprocess.run([sys.executable,SCRIPTS/'verify_h3_alignment_session.py',manifest,'--root',root],capture_output=True,text=True)
   self.assertEqual(verify().returncode,0,verify().stdout+verify().stderr)
   receipt=session/'integrity_audit.json';written=subprocess.run([sys.executable,SCRIPTS/'verify_h3_alignment_session.py',manifest,'--root',root,'--write-audit-receipt',receipt,'--validated-at-utc','2026-07-31T12:00:00Z'],capture_output=True,text=True);self.assertEqual(written.returncode,0,written.stdout+written.stderr);checked=subprocess.run([sys.executable,SCRIPTS/'verify_h3_alignment_session.py','--root',root,'--verify-audit-receipt',receipt],capture_output=True,text=True);self.assertEqual(checked.returncode,0,checked.stdout+checked.stderr)
   payload['raw_artifacts']['uart_raw']['sha256']=uart_hash+'0';manifest.write_text(json.dumps(payload));rejected=verify();self.assertEqual(rejected.returncode,2);self.assertIn('raw_artifacts.uart_raw.sha256 must be exactly 64 lowercase hex characters',rejected.stdout)
 def test_perturbation_dry_run_and_rejects_bad_provenance(self):
  with tempfile.TemporaryDirectory() as raw:
   d=Path(raw);base={'timer_period_mean_s':1,'timer_period_p95_s':1,'timer_period_stddev_s':.1,'isr_duration_mean_s':.1,'execution_window_mean_s':.2,'execution_window_stddev_s':.01,'uart_drop_count':0,'trace_drop_count':0,'overflow_count':0,'artifact_hashes':{'observer_sha256':'a'*64}}
   paths=[]
   for variant in ('BASE','GPIO_ONLY','GPIO_UART','GPIO_UART_RECORDER'):
    p=d/(variant+'.json');p.write_text(json.dumps({**base,'variant':variant}));paths.append(p)
   base_path=paths[0];static={**base,'variant':'BASE','timer_period_mean_s':None,'timer_period_p95_s':None,'timer_period_stddev_s':None,'isr_duration_mean_s':None,'execution_window_mean_s':None,'execution_window_stddev_s':None,'uart_drop_count':None,'trace_drop_count':None,'overflow_count':None};base_path.write_text(json.dumps(static));out=d/'out.json';r=subprocess.run([sys.executable,SCRIPTS/'analyze_perturbation.py',*paths,'--output',out],capture_output=True,text=True);self.assertEqual(r.returncode,0,r.stderr);result=json.loads(out.read_text());self.assertEqual(result['schema_version'],'phase1-perturbation-v2');self.assertEqual(result['comparisons']['BASE']['timer_period_mean_s']['status'],'not_observable');self.assertEqual(result['comparisons']['GPIO_ONLY']['timer_period_mean_s']['status'],'baseline_not_observable')
   bad=d/'bad.json';bad.write_text(json.dumps({**base,'variant':'BAD','artifact_hashes':{'observer_sha256':'bad'}}));r=subprocess.run([sys.executable,SCRIPTS/'analyze_perturbation.py',paths[0],bad,'--output',d/'badout.json'],capture_output=True,text=True);self.assertNotEqual(r.returncode,0)
if __name__=='__main__': unittest.main()
