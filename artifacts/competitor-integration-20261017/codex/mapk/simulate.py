import json
from pathlib import Path

import libsbml
import numpy as np
import roadrunner


def main():
    model_path = Path(__file__).with_name('model.xml')
    document = libsbml.readSBMLFromFile(str(model_path))
    model = document.getModel()
    if model is None:
        raise ValueError('Unable to read SBML model')
    species_ids = [
        species.getId()
        for species in model.getListOfSpecies()
        if not species.getBoundaryCondition() and not species.getConstant()
    ]
    simulator = roadrunner.RoadRunner(str(model_path))
    simulator.integrator.relative_tolerance = 1e-10
    simulator.integrator.absolute_tolerance = 1e-12
    simulator.timeCourseSelections = ['time'] + species_ids
    result = np.asarray(simulator.simulate(0, 1800, 121))
    if result.shape != (121, len(species_ids) + 1):
        raise ValueError('Unexpected simulation output shape')
    if not np.isfinite(result).all():
        raise ValueError('Non-finite simulation output')
    print(json.dumps({
        'times': result[:, 0].tolist(),
        'values': {
            species_id: result[:, index + 1].tolist()
            for index, species_id in enumerate(species_ids)
        },
    }, allow_nan=False))


if __name__ == '__main__':
    main()
