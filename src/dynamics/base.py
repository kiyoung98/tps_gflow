import numpy as np
import openmm.unit as unit
from abc import abstractmethod, ABC
from scipy.constants import physical_constants


class BaseDynamics(ABC):
    def __init__(self, args, state):
        super().__init__()
        self.device = args.device
        self.temperature = args.temperature * unit.kelvin
        self.friction_coefficient = args.friction_coefficient / unit.picoseconds
        self.timestep = args.timestep * unit.femtoseconds
        self.start_file = f'./data/{args.molecule}/{state}.pdb'

        self.simulation, self.external_force = self.setup()

        self.simulation.minimizeEnergy()
        self.position = self.report()[0]
        self.reset()
        
        self.num_particles = self.simulation.system.getNumParticles()
        self.v_scale, self.f_scale, self.masses, self.std = self.get_md_info()
        
    @abstractmethod
    def setup(self):
        pass

    def get_md_info(self):
        v_scale = np.exp(-self.timestep * self.friction_coefficient)
        f_scale = (1 - v_scale) / self.friction_coefficient

        masses = [self.simulation.system.getParticleMass(i).value_in_unit(unit.dalton) for i in range(self.num_particles)]
        masses = unit.Quantity(np.array(masses), unit.dalton)
        
        unadjusted_variance = unit.BOLTZMANN_CONSTANT_kB * 300 * unit.kelvin * (1 - v_scale ** 2) / masses[:, None]
        std_SI_units = 1 / physical_constants['unified atomic mass unit'][0] * unadjusted_variance.value_in_unit(unit.joule / unit.dalton)
        
        std = unit.Quantity(np.sqrt(std_SI_units), unit.meter / unit.second)
        return v_scale, f_scale, masses, std
    
    def step(self, forces):
        for i in range(forces.shape[0]):
            self.external_force.setParticleParameters(i, i, forces[i])
        self.external_force.updateParametersInContext(self.simulation.context)
        self.simulation.step(1)

    def report(self):
        state = self.simulation.context.getState(getPositions=True, getEnergy=True)
        positions = state.getPositions().value_in_unit(unit.nanometer)
        potentials = state.getPotentialEnergy().value_in_unit(unit.kilojoules/unit.mole)
        return positions, potentials

    def reset(self):
        for i in range(len(self.position)):
            self.external_force.setParticleParameters(i, i, [0, 0, 0])
        self.external_force.updateParametersInContext(self.simulation.context)
        self.simulation.context.setPositions(self.position)
        self.simulation.context.setVelocitiesToTemperature(0)