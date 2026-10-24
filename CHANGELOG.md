## 1.0.0-beta.1 (2026-10-24)

### Features

* log startup, requests, and the deep-link/load path ([5859fda](https://github.com/equa/equa-cfd-viewer/commit/5859fda8b0a2ae8d083664e618e73629f6f70178))
* preselect a case from ?case=<name> for backend embedding ([3d49c69](https://github.com/equa/equa-cfd-viewer/commit/3d49c69360eeb6c06d2d203703d25e91b957901b))
* read decomposed cases with vtkPOpenFOAMReader ([690eeb7](https://github.com/equa/equa-cfd-viewer/commit/690eeb7d183afb37eb3f89651037bb38dcf4ad9f))
* refresh time steps (button + on reopen) ([4e47034](https://github.com/equa/equa-cfd-viewer/commit/4e47034235797c7b0025ccd438bca8fa1bf6aa3c))
* run headless against an empty/dynamic case root (service mode) ([7244893](https://github.com/equa/equa-cfd-viewer/commit/7244893a863ba7a1d00c12ad183817f41c01da98))
* **server:** add /health, /api/config and --mode ([5885b61](https://github.com/equa/equa-cfd-viewer/commit/5885b610b9a601ebc49fff9b7357eea45c3bcbca))
* **server:** case sources with stable ids, labels and cheap cell counts ([453ec16](https://github.com/equa/equa-cfd-viewer/commit/453ec16ce9e06e59e258d245354549a2d39c4ed9))
* **server:** serve under ROOT_PATH and take settings from the environment ([9019b7f](https://github.com/equa/equa-cfd-viewer/commit/9019b7f4a4e540a1d7e9c00a45c83a5d8664336f))
* **web:** a start screen, and cases opened by id ([bb6d41c](https://github.com/equa/equa-cfd-viewer/commit/bb6d41c21f2ef1a4f4e98f631de64a37930a89f7))

### Bug Fixes

* **case:** read cases in place, without writing a .foam marker into them ([4d8eeeb](https://github.com/equa/equa-cfd-viewer/commit/4d8eeeba51207913f9108e8ce612005f87e7b3ac))
* empty-case startup crash that restart-loops the container ([6a3e688](https://github.com/equa/equa-cfd-viewer/commit/6a3e6880c56e7877e3d0b0e4bb62eeeec2381455))
* keep the equa-dev feed out of the image build ([84fdf16](https://github.com/equa/equa-cfd-viewer/commit/84fdf16bd5cd6fe2aae316e3a8b68971002569e8))
* middleware second parameter must be named `handler` ([99d226a](https://github.com/equa/equa-cfd-viewer/commit/99d226aa42b6d0c7c784647f263a2400d558fdba))
* **pipeline:** restore rendering_started for cfd-backend's smoke test ([14603bd](https://github.com/equa/equa-cfd-viewer/commit/14603bd4b93800afa63d3f8c5f39d587005c28ab))
* rebuild the field selectors from the loaded time step ([070fbc1](https://github.com/equa/equa-cfd-viewer/commit/070fbc199ce863c6fef6a826df5e89fa9da05295))
* **server:** find cases where cfd-backend finds them, bounded at 4 levels ([052dac1](https://github.com/equa/equa-cfd-viewer/commit/052dac1d3d58a25c040311bc85e0ec510b037511))
* **server:** report unreadable directories instead of hiding or failing on them ([473ae36](https://github.com/equa/equa-cfd-viewer/commit/473ae362d8c4add6317bc13484a5c680035132ec))
* streamline lines/tubes via two actors (not mapper-input swap); bands clamp ([86967fc](https://github.com/equa/equa-cfd-viewer/commit/86967fc5201aea0c4d2d6269565d9a4a8e0c3ba8))

### Performance Improvements

* build the render window lazily, so extraction needs no GL backend ([f63662f](https://github.com/equa/equa-cfd-viewer/commit/f63662fcde2120183fd05d6060d6029b5aeadce2))
* don't re-bake FoamVizColor on every scene update (cheap toggles) ([dd8339f](https://github.com/equa/equa-cfd-viewer/commit/dd8339fa4fc4408d9fbf1f27e1aba73009500f13))
* don't re-set active vectors every scene update (the real toggle cost) ([7dd1f3a](https://github.com/equa/equa-cfd-viewer/commit/7dd1f3a6507c8519c82a02099a0f0e31a4030d70))
* free the previous case on a switch, and trim the heap ([4a58f11](https://github.com/equa/equa-cfd-viewer/commit/4a58f11471aa098337d57fee02e6178beae75968))
* make reopening the viewer cheap (don't re-read the mesh unless a step appeared) ([72b5754](https://github.com/equa/equa-cfd-viewer/commit/72b575456c0a48a84232604c78c2dcea098265ae))
