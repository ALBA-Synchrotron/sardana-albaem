#!/usr/bin/env python
import time

from sardana import State, DataAccess
from sardana.pool import AcqSynch
from sardana.pool.controller import CounterTimerController, Type, Access, \
    Description, Memorize, Memorized, NotMemorized
from sardana.sardanavalue import SardanaValue

from sardana_albaem.ctrl.em2 import Em2


__all__ = ['Albaem2CoTiCtrl']

TRIGGER_INPUTS = {'DIO_1': 0, 'DIO_2': 1, 'DIO_3': 2, 'DIO_4': 3,
                  'DIFF_IO_1': 4, 'DIFF_IO_2': 5, 'DIFF_IO_3': 6,
                  'DIFF_IO_4': 7, 'DIFF_IO_5': 8, 'DIFF_IO_6': 9,
                  'DIFF_IO_7': 10, 'DIFF_IO_8': 11, 'DIFF_IO_9': 12}


class Albaem2CoTiCtrl(CounterTimerController):
    MaxDevice = 5

    ctrl_properties = {
        'AlbaEmHost': {
            Description: 'AlbaEm Host name',
            Type: str
        },
        'Port': {
            Description: 'AlbaEm Host name',
            Type: int
        },
    }

    ctrl_attributes = {
        'ExtTriggerInput': {
            Type: str,
            Description: 'ExtTriggerInput',
            Access: DataAccess.ReadWrite,
            Memorize: Memorized
        },
        'AcquisitionMode': {
            Type: str,
            # TODO define the modes names ?? (I_AVGCURR_A, Q_CHARGE_C)
            Description: 'Acquisition Mode: CHARGE, INTEGRATION',
            Access: DataAccess.ReadWrite,
            Memorize: Memorized
        },
    }

    axis_attributes = {
        "Range": {
            Type: str,
            Description: 'Range for the channel',
            Memorize: NotMemorized,
            Access: DataAccess.ReadWrite,
        },
        "Inversion": {
            Type: bool,
            Description: 'Channel Digital inversion',
            Memorize: NotMemorized,
            Access: DataAccess.ReadWrite,

        },
        "InstantCurrent": {
            Type: float,
            Description: 'Channel instant current',
            Memorize: NotMemorized,
            Access: DataAccess.ReadOnly
        },
        "Formula":
            {
                Type: str,
                Description: 'The formula to get the real value.\n '
                             'e.g. "(value/10)*1e-06"',
                Access: DataAccess.ReadWrite
            },
    }

    def __init__(self, inst, props, *args, **kwargs):
        """Class initialization."""
        CounterTimerController.__init__(self, inst, props, *args, **kwargs)
        msg = "__init__(%s, %s): Entering...", repr(inst), repr(props)
        self._log.debug(msg)

        self._em2 = Em2(self.AlbaEmHost, self.Port)
        self._em2_software_version = self._em2.software_version
        if (not isinstance(self._em2_software_version, tuple) 
                or len(self._em2_software_version) != 3):
            raise ValueError("software version format must be (x, y, z)")
        self._synchronization = AcqSynch.SoftwareTrigger
        self._latency_time = 0.001  # In fact, it is just 320us
        self._use_sw_trigger = True
        self._started = False
        self._aborted = False
        self._nb_points_read_per_start = 0
        self._nb_points_expected_per_start = 0
        self._nb_points_fetched = 0
        self._new_data = {}
        self._nb_start = 0
        self._state = State.On
        self._status = 'On'

        self.formulas = {1: 'value', 2: 'value', 3: 'value', 4: 'value'}

    def _clean_variables(self):
        status = self._em2.acquisition_state
        if status in ['ACQUIRING', 'RUNNING']:
            self._em2.stop_acquisition()

        self._use_sw_trigger = True
        self._new_data = {}
        self._started = False
        self._aborted = False
        self._nb_points_fetched = 0
        self._nb_points_read_per_start = 0
        self._nb_points_expected_per_start = 0

    def axis_channel(self, axis):
        """Return EM2 Channel object for the given controller axis"""
        return self._em2[axis - 2]

    def StateAll(self):
        """Read state of all axis."""
        hardware_state = self._em2.acquisition_state
        self._log.debug('HW status %s', hardware_state)

        allowed_states = ['ACQUIRING', 'RUNNING', 'ON', 'FAULT']
        if hardware_state == 'FAULT' or hardware_state not in allowed_states:
            self._state = State.Fault
            self._status = hardware_state
            return

        read_ready = self._nb_points_read_per_start == self._nb_points_expected_per_start
        if read_ready or self._aborted or not self._started:
            self._state = State.On
            self._status = 'ON'
        else:
            self._state = State.Moving
            self._status = 'MOVING'
            if hardware_state == 'ON':
                self._log.warning('Data not ready, but HW status is ON - forcing ReadAll')
                self.ReadAll()

    def StateOne(self, axis):
        """Read state of one axis."""
        return self._state, self._status

    def PrepareOne(self, axis, value, repetitions, latency, nb_starts):
        # Protection for the integration time
        if value < 1e-4:
            raise ValueError('The minimum integration time is 0.1 ms')

        if self._synchronization in [AcqSynch.HardwareStart]:
            raise ValueError('The Start synchronization is not allowed yet')

        self._clean_variables()
        self._nb_points_expected_per_start = repetitions
        nb_points = repetitions * nb_starts
        self._acq_time = value
        latency_time = latency

        # Select the trigger mode according to the synchronization mode

        if self._synchronization in [AcqSynch.SoftwareGate,
                                     AcqSynch.SoftwareTrigger]:
            mode = 'SOFTWARE'
            self._use_sw_trigger = True
        elif self._synchronization == AcqSynch.SoftwareStart:
            mode = 'AUTOTRIGGER'
            self._use_sw_trigger = True
        elif self._synchronization == AcqSynch.HardwareTrigger:
            mode = 'HARDWARE'
            self._use_sw_trigger = False
        elif self._synchronization == AcqSynch.HardwareGate:
            mode = 'GATE'
            self._use_sw_trigger = False
        else:
            raise ValueError(
                'Unsupported synchronization mode: {0}'.format(self._synchronization)
            )

        # Configure the electrometer
        self._em2.acquisition_time = self._acq_time
        self._em2.trigger_mode = mode
        self._em2.nb_points = nb_points
        # This controller is not ready to use the timestamp
        self._em2.timestamp_data = False

    def LoadOne(self, axis, integ_time, repetitions, latency_time):
        # Configure the electrometer on the PrepareOne
        pass

    def PreStartOne(self, axis, value):
        # Check if the communication is stable before start
        try:
            _ = self._em2.acquisition_state
        except Exception:
            self._log.error('There is not connection to the electrometer.')
            return False
        return True

    def StartAll(self):
        self._nb_points_read_per_start = 0
        if not self._started:
            self._em2.start_acquisition(soft_trigger=False)
            self._started = True
        if self._use_sw_trigger:
            self._em2.software_trigger()

    def ReadAll(self):
        # TODO Change the ACQU:MEAS command by CHAN:CURR
        nb_points_ready = self._em2.nb_points_ready
        if self._nb_points_fetched < nb_points_ready:
            data_len = nb_points_ready - self._nb_points_fetched
            self._nb_points_read_per_start += data_len
            self._new_data = self._em2.read(self._nb_points_fetched, data_len)
            try:
                for axis in range(1, 5):
                    formula = self.formulas[axis]
                    if formula.lower() != 'value':
                        channel = 'CHAN0{0}'.format(axis)
                        values = self._new_data[channel]
                        values = [eval(formula, {'value': val}) for val
                                  in values]
                        self._new_data[channel] = values

                self._new_data['CHAN00'] = [self._acq_time] * data_len
                self._nb_points_fetched = nb_points_ready
            except Exception as e:
                raise Exception('ReadAll error: {0}'.format(e))

    def ReadOne(self, axis):
        # self._log.debug("ReadOne(%d): Entering...", axis)
        if len(self._new_data) == 0:
            return None
        axis -= 1
        channel = 'CHAN0{0}'.format(axis)
        values = list(self._new_data[channel])


        if self._synchronization in [AcqSynch.SoftwareTrigger,
                                AcqSynch.SoftwareGate]:
            # Fix issue with the electromether (EL-15157)  pow(2, np.log2(np.ceil(int_time/2.61)))
            if (axis != 0 
                    and self._em2_software_version >= (2, 0, 0)
                    and self._em2_software_version < (2, 1, 0)):
                factor = pow(2,int.bit_length(int(self._acq_time/2.621441)))
            else:
                factor = 1
            self._log.debug('ReadOne value: {}, tune {}'.format(values[0], factor))
            return SardanaValue(values[0] * factor)

        else:
            self._new_data[channel] = []
            return values

    def AbortOne(self, axis):
        if not self._aborted:
            self._aborted = True
            self._em2.stop_acquisition()

###############################################################################
#                Axis Extra Attribute Methods
###############################################################################

    def GetAxisExtraPar(self, axis, name):
        self._log.debug("GetExtraAttributePar(%d, %s): Entering...", axis,
                        name)
        if axis == 1:
            raise ValueError('The axis 1 does not use the extra attributes')

        name = name.lower()
        channel = self.axis_channel(axis)
        if name == "range":
            return channel.range
        elif name == 'inversion':
            return channel.inversion
        elif name == 'instantcurrent':
            return channel.current
        elif name == 'formula':
            return self.formulas[axis-1]

    def SetAxisExtraPar(self, axis, name, value):
        if axis == 1:
            raise ValueError('The axis 1 does not use the extra attributes')

        name = name.lower()
        channel = self.axis_channel(axis)
        if name == "range":
            channel.range = value
        elif name == 'inversion':
            channel.inversion = int(value)
        elif name == 'formula':
            self.formulas[axis-1] = value


###############################################################################
#                Controller Extra Attribute Methods
###############################################################################

    def SetCtrlPar(self, parameter, value):
        param = parameter.lower()
        if param == 'exttriggerinput':
            self._em2.trigger_input = value
        elif param == 'acquisitionmode':
            self._em2.acquisition_mode = value
        else:
            CounterTimerController.SetCtrlPar(self, parameter, value)

    def GetCtrlPar(self, parameter):
        param = parameter.lower()
        if param == 'exttriggerinput':
            value = self._em2.trigger_input
        elif param == 'acquisitionmode':
            value = self._em2.acquisition_mode
        else:
            value = CounterTimerController.GetCtrlPar(self, parameter)
        return value


def main():
    host = 'electproto38'
    port = 6025
    ctrl = Albaem2CoTiCtrl('test', {'AlbaEmHost': host, 'Port': port})
    ctrl.AddDevice(1)
    ctrl.AddDevice(2)
    ctrl.AddDevice(3)
    ctrl.AddDevice(4)
    ctrl.AddDevice(5)

    print("LATENCY TIME: ", ctrl._em2.lowtime)

    ctrl._synchronization = AcqSynch.SoftwareStart # AcqSynch.SoftwareTrigger
    # ctrl._synchronization = AcqSynch.HardwareTrigger
    acqtime = 0.001
    repetitions = 10
    ctrl.PrepareOne(1, acqtime, repetitions, 0.1, 1)
    t0 = time.time()
    ctrl.StartAll()
    ctrl.StateAll()
    while ctrl.StateOne(1)[0] != State.On:
        ctrl.StateAll()
        time.sleep(0.1)
    print(time.time() - t0)
    ctrl.ReadAll()
    print(ctrl.ReadOne(2))
    return ctrl


if __name__ == '__main__':
    main()
